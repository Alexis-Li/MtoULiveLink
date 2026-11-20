# ruff: noqa: E402
"""Transport tests: the worker must honour the endpoint the config reports.

The peers and the host checks point the Maya adapter at the port the host
listener actually uses by setting `mtou_config.PORT`; the worker therefore reads
the endpoint from the config module at call time instead of binding a copy at
import time. This module pins that behaviour with a real socket.
"""

import pathlib
import socket
import sys
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mtou_livelink import config as mtou_config  # noqa: E402
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
