# ruff: noqa: E402
"""Transport tests: the worker must honour the endpoint the config reports.

The peers and the host checks point the Maya adapter at the port the host
listener actually uses by setting `mtou_config.PORT`; the worker therefore reads
the endpoint from the config module at call time instead of binding a copy at
import time. This module pins that behaviour with a real socket.
"""

import pathlib
import queue
import socket
import sys
import threading
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mtou_livelink import config as mtou_config  # noqa: E402
from mtou_livelink import protocol as mtou_protocol
from mtou_livelink import session as mtou_session  # noqa: E402


class EndpointTests(unittest.TestCase):
    def test_worker_connects_to_the_endpoint_the_config_reports(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5.0)
        port = listener.getsockname()[1]
        self.addCleanup(listener.close)

        worker = mtou_session.SenderWorker({"type": "init", "revision": 1})
        with mock.patch.object(mtou_config, "PORT", port), \
                mock.patch.object(mtou_config, "HOST", "127.0.0.1"):
            sock = worker._connect()
        self.assertIsNotNone(sock, "the worker refused the configured endpoint")
        self.addCleanup(sock.close)
        accepted, address = listener.accept()
        self.addCleanup(accepted.close)
        self.assertEqual("127.0.0.1", address[0])
        # The worker's own socket points at the listener the config reported.
        self.assertEqual(("127.0.0.1", port), sock.getpeername())


class RealtimeMetricsTests(unittest.TestCase):
    SESSION_ID = 17
    SNAPSHOT = {"type": "realtime_metrics", "session_id": SESSION_ID,
                "set_revision": 1, "editor_fps": 56}

    def connect(self, peer_capability=True, maya_capability=True):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5.0)
        hello = mtou_protocol.make_hello_message(
            "2024", "cm", 1, 24, capabilities={"realtime_metrics": maya_capability},
            maya_subject_id="test-maya")
        init = {"type": "init", "object_id": "character", "revision": 9,
                "set_revision": 1, "space_revision": 1}
        self.replies = queue.Queue()

        def on_reply(reply):
            self.replies.put(reply["type"])
            return True

        worker = mtou_session.SenderWorker(init, hello, on_reply=on_reply)
        self.addCleanup(worker.join, 5.0)
        self.addCleanup(worker.stop)
        with mock.patch.object(mtou_config, "PORT", listener.getsockname()[1]):
            worker.start()
            self.peer, _ = listener.accept()
        self.addCleanup(self.peer.close)
        self.peer.settimeout(5.0)
        self.assertEqual(hello, mtou_protocol.recv_message(self.peer))
        caps = {}
        ack = {"type": "hello_ack", "version": mtou_config.PROTOCOL_VERSION,
               "session_id": self.SESSION_ID, "source_instance_id": "test-source",
               "capabilities": caps, "targets": [], "space": {"anchor": {}}}
        if peer_capability is not None:
            caps["realtime_metrics"] = peer_capability
            ack.update(ue_host_version="5.7.4-test", ue_plugin_version="1.1-test")
        self.peer.sendall(mtou_protocol.encode_message(ack))
        self.assertEqual(init, mtou_protocol.recv_message(self.peer))
        self.assertEqual(ack, worker.hello_reply())
        ready = dict(init, type="ready", target_instance_id="test-target",
                     session_id=self.SESSION_ID, workflow="animation",
                     missing_in_unreal=[], missing_in_maya=[], bone_name_remaps=[],
                     target_morph_count=0, accepted_morph_count=0)
        self.exchange(ready)
        return worker

    def exchange(self, *replies):
        barrier = {"type": "error", "code": "GROUP_NOT_READY", "kind": "scheduling",
                   "message": "test marker", "details": ""}
        self.peer.sendall(b"".join(mtou_protocol.encode_message(reply)
                                   for reply in (*replies, barrier)))
        while self.replies.get(timeout=5.0) != "error":
            pass

    def assert_metrics(self, worker, fps=None, apply_ms=None):
        metrics = worker.realtime_metrics()
        self.assertIsNotNone(metrics)
        self.assertEqual(fps, metrics["ue_editor_fps"])
        self.assertEqual(apply_ms, metrics["ue_apply_ms"])

    def test_only_the_current_ready_set_can_publish_zero_or_a_sample(self):
        worker = self.connect()
        self.assert_metrics(worker)
        self.exchange(dict(self.SNAPSHOT, editor_fps=0, apply_ms=0))
        self.assert_metrics(worker, 0, 0)
        self.exchange(dict(self.SNAPSHOT, session_id=self.SESSION_ID - 1),
                      dict(self.SNAPSHOT, set_revision=2))
        self.assert_metrics(worker, 0, 0)
        self.exchange(self.SNAPSHOT)
        self.assert_metrics(worker, 56)
        worker.submit_control(mtou_protocol.make_remove_object_message(
            self.SESSION_ID, "secondary", 2, ""), discard_latest=True)
        self.assertEqual("remove_object", mtou_protocol.recv_message(self.peer)["type"])
        self.exchange(self.SNAPSHOT)
        self.assert_metrics(worker)
        self.exchange({"type": "object_removed", "session_id": self.SESSION_ID,
                       "set_revision": 2, "object_id": "secondary",
                       "restored": True, "detail": ""}, self.SNAPSHOT)
        self.assert_metrics(worker)
        self.exchange(dict(self.SNAPSHOT, set_revision=2, apply_ms=1.25))
        self.assert_metrics(worker, 56, 1.25)
        received = threading.Event()
        receive = worker._receive_realtime_metrics

        def receive_metrics(reply):
            receive(reply)
            received.set()

        ending = {"type": "restart_state", "state": "ending",
                  "session_id": self.SESSION_ID, "code": "", "detail": "input changed"}
        with mock.patch.object(worker, "_receive_realtime_metrics", side_effect=receive_metrics):
            self.peer.sendall(mtou_protocol.encode_message(ending)
                              + mtou_protocol.encode_message(dict(self.SNAPSHOT, set_revision=2)))
            self.assertTrue(received.wait(5.0))
        self.assertIsNone(worker.realtime_metrics())

    def test_mode_acknowledgements_fence_in_flight_snapshots(self):
        worker = self.connect()
        self.exchange(self.SNAPSHOT)
        self.assert_metrics(worker, 56)
        worker.begin_ordered()
        worker.submit_ordered(mtou_protocol.make_cache_clear_message(1))
        worker.end_ordered(discard_pending=False)
        self.assertEqual("cache_clear", mtou_protocol.recv_message(self.peer)["type"])
        self.exchange(self.SNAPSHOT)
        self.assert_metrics(worker)
        self.exchange({"type": "cache_cleared", "clear_id": 1, "upload_id": 0, "play_id": 0},
                      self.SNAPSHOT)
        self.assert_metrics(worker, 56)

        linkage = {"type": "mode_state", "session_id": self.SESSION_ID, "mode_revision": 1,
                   "mode": "linkage", "control": "unreal", "detail": "",
                   "anchor": {"location_cm": [0, 0, 0],
                              "rotation_deg": {"roll": 0, "pitch": 0, "yaw": 0}}}
        self.exchange(linkage, self.SNAPSHOT)
        self.assert_metrics(worker)
        worker.begin_ordered()
        worker.submit_control(mtou_protocol.make_leave_linkage_message(
            self.SESSION_ID, 2, "end", ""))
        worker.end_ordered(discard_pending=False)
        self.assertEqual("leave_linkage", mtou_protocol.recv_message(self.peer)["type"])
        self.exchange(dict(linkage, mode="realtime", control="maya"), self.SNAPSHOT)
        self.assert_metrics(worker)
        self.exchange(dict(linkage, mode="realtime", control="maya", mode_revision=2),
                      dict(self.SNAPSHOT, apply_ms=0.5))
        self.assert_metrics(worker, 56, 0.5)
        worker.stop()
        self.assertIsNone(worker.realtime_metrics())

    def test_a_previous_unreal_greeting_leaves_metrics_unknown(self):
        worker = self.connect(peer_capability=None)
        self.assertNotIn("ue_host_version", worker.hello_reply())
        self.exchange(self.SNAPSHOT)
        self.assert_metrics(worker)

    def test_unreal_offering_metrics_alone_does_not_enable_them(self):
        worker = self.connect(maya_capability=False)
        self.exchange(self.SNAPSHOT)
        self.assert_metrics(worker)


class ControlDeliveryTests(unittest.TestCase):
    """A control statement is delivered whichever delivery mode is on."""

    def test_control_in_latest_mode_precedes_live_frames(self):
        worker = mtou_session.SenderWorker({"type": "init", "revision": 1})
        worker.submit({"type": "frame", "serial": 1})
        worker.submit_control({"type": "leave_linkage", "target": "end"})
        # Latest mode never drains the ordered queue; the carryover does.
        self.assertEqual(0, worker.ordered_pending())
        packet, pending, realtime = worker._pop_stream_packet()
        self.assertIn(b"leave_linkage", packet)
        self.assertFalse(realtime)
        pending.task_done()

    def test_control_in_ordered_mode_keeps_its_place(self):
        worker = mtou_session.SenderWorker({"type": "init", "revision": 1})
        worker.begin_ordered()
        worker.submit_control({"type": "mode", "transition": "ready"})
        self.assertEqual(1, worker.ordered_pending())
        self.assertTrue(worker._carryover.empty())

    def test_realtime_counters_distinguish_replacement_send_and_discard(self):
        worker = mtou_session.SenderWorker(None)
        worker._set_status("ready", "Connected")
        worker.submit({"type": "frame", "serial": 1})
        worker.submit({"type": "frame", "serial": 2})
        worker.submit_control({"type": "remove_object"}, discard_latest=True)
        worker.submit({"type": "frame", "serial": 3})
        worker.begin_ordered()
        worker.submit({"type": "frame", "serial": 4})

        def send(sock, packet, *args, **kwargs):
            if b'"serial":4' in packet:
                worker._stop_event.set()
                return False
            return True

        with mock.patch.object(worker, "_send_packet", side_effect=send), \
                mock.patch.object(mtou_session.select, "select", return_value=([], [], [])), \
                mock.patch.object(mtou_session.time, "monotonic", return_value=10.0):
            worker._run_connected(None)
        worker._stop_event.clear()  # Inspect the final ready counters.
        with mock.patch.object(mtou_session.time, "monotonic", return_value=10.5):
            metrics = worker.realtime_metrics()
        self.assertEqual(1, metrics["replaced_frames"])
        self.assertEqual(1, metrics["sent_frames"])
        self.assertEqual(1, metrics["sent_hz"])
        self.assertEqual(1, metrics["stages"]["send"]["count"])
        with mock.patch.object(mtou_session.time, "monotonic", return_value=11.1):
            self.assertEqual(0, worker.realtime_metrics()["sent_hz"])


if __name__ == "__main__":
    unittest.main()
