# ruff: noqa: E402
"""Mode transitions through the real panel, session, sender loop, cache and linkage.

Only Maya sampling, timers, controls and the socket are replaced: the sender
thread's loop runs against a fake socket that plays a minimal Unreal, so these
tests read what actually reaches the wire and in which order.
"""

import collections
import json
import pathlib
import select
import struct
import sys
import threading
import time
import types
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mtou_livelink import config as mtou_config
from mtou_livelink import controller as mtou_controller
from mtou_livelink import protocol as mtou_protocol
from mtou_livelink import scene as mtou_scene
from mtou_livelink import session as mtou_session

SESSION_ID = 4242
REVISION = 7
BIND = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
WAIT_SECONDS = 5.0


class FakeScene(object):
    """One sampleable character; Maya's scene is not involved."""

    def snapshot(self):
        return mtou_scene.CharacterSnapshot(
            REVISION, "|root", "Clothes", [("root", -1)], ["Smile"], (), (), [BIND], 0)

    def sample(self):
        return mtou_scene.CharacterFrame(REVISION, [list(BIND)], [0.0])

    def close(self):
        pass


class FakeHost(object):
    """The Maya linkage host adapter, without Maya."""

    def stale_reference(self, anchor):
        del anchor
        return None

    def capture_state(self):
        return {"time": 1.0}

    def restore_state(self, state):
        del state
        return []

    def sample_group(self, object_ids):
        return [{"object_id": object_ids[0], "transforms": [BIND], "curves": [0.0]}]


class UnrealSocket(object):
    """The socket the sender thread owns, played by a minimal Unreal.

    It records every message Maya sends after the handshake and confirms each
    ``cache_clear`` the way Unreal does.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._inbound = bytearray()
        self.sent = []
        self.closed = False
        # While set, cache clears are recorded but not yet confirmed.
        self.held_clears = None
        self.hung_up = False
        self.push({"type": "hello_ack", "version": mtou_config.PROTOCOL_VERSION,
                   "session_id": SESSION_ID,
                   "capabilities": dict(mtou_protocol.DEFAULT_CAPABILITIES),
                   "targets": [],
                   "space": {"space_revision": 1,
                             "anchor": {"translation": [0.0, 0.0, 0.0],
                                        "rotation": [0.0, 0.0, 0.0, 1.0],
                                        "scale": 1.0}}})
        self.push({"type": "ready", "session_id": SESSION_ID,
                   "object_id": mtou_config.CHARACTER_OBJECT_ID,
                   "set_revision": 1, "space_revision": 1, "revision": REVISION,
                   "missing_in_unreal": [], "missing_in_maya": [],
                   "bone_name_remaps": [], "workflow": "animation",
                   "target_morph_count": 1, "accepted_morph_count": 1})

    def push(self, message):
        with self._lock:
            self._inbound += mtou_protocol.encode_message(message)

    def readable(self):
        with self._lock:
            return bool(self._inbound) or self.hung_up

    def hang_up(self):
        """Unreal closes the connection once what it already sent is read."""
        with self._lock:
            self.hung_up = True

    def settimeout(self, timeout):
        del timeout

    def recv(self, size):
        with self._lock:
            chunk = bytes(self._inbound[:size])
            del self._inbound[:size]
        return chunk

    def sendall(self, packet):
        while packet:
            size = struct.unpack(">Q", packet[:8])[0]
            message = json.loads(packet[8:8 + size].decode("utf-8"))
            packet = packet[8 + size:]
            with self._lock:
                self.sent.append(message)
            if message.get("type") == "cache_clear":
                if self.held_clears is not None:
                    self.held_clears.append(message["clear_id"])
                else:
                    self.confirm_clear(message["clear_id"])

    def confirm_clear(self, clear_id):
        self.push({"type": "cache_cleared", "upload_id": 0, "play_id": 0,
                   "clear_id": clear_id})

    def shutdown(self, how):
        del how

    def close(self):
        self.closed = True

    def types(self):
        with self._lock:
            return [message["type"] for message in self.sent]


class ConnectionHarness(unittest.TestCase):
    """A connected panel whose sender thread talks to :class:`UnrealSocket`."""

    def setUp(self):
        self.deferred = collections.deque()
        self.timers = {}
        self.timer_ids = iter(range(1, 1000000))
        self.socket = UnrealSocket()
        self.fake_cmds = self._fake_cmds()
        self.fake_om = self._fake_om()
        for module in list(sys.modules.values()):
            name = getattr(module, "__name__", "")
            if not name.startswith("mtou_livelink"):
                continue
            for binding, value in (("cmds", self.fake_cmds), ("om", self.fake_om)):
                if hasattr(module, binding):
                    patcher = mock.patch.object(module, binding, value)
                    patcher.start()
                    self.addCleanup(patcher.stop)
        harness = self
        real_select = select.select

        def fake_select(readers, writers, errors, timeout):
            if readers and readers[0] is harness.socket:
                if harness.socket.readable():
                    return ([harness.socket], [], [])
                time.sleep(0.001)
                return ([], [], [])
            return real_select(readers, writers, errors, timeout)

        for patcher in (
                mock.patch.object(mtou_session.SenderWorker, "_connect",
                                  lambda worker: harness.socket),
                mock.patch.object(mtou_session.SenderWorker, "_send_initial",
                                  lambda worker, sock: True),
                mock.patch.object(select, "select", fake_select),
                mock.patch.object(mtou_controller.Controller, "_linkage_host",
                                  lambda controller, scene, ids, rate: FakeHost()),
                mock.patch.object(mtou_controller.Controller, "_show_error",
                                  lambda controller, diagnostic, session=False:
                                  harness.errors.append(diagnostic))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.errors = []
        self.controller = mtou_controller.Controller()
        self.controller._scene = FakeScene()
        holder = {}
        # Started the way the panel's connect() starts it.
        self.session = mtou_session.StreamingSession.start(
            self.controller._scene, 24.0,
            lambda event: self.controller._on_streaming_session_event(
                holder.get("session"), event),
            on_statement=lambda reply: self.controller._on_session_statement(
                holder, reply))
        holder["session"] = self.session
        self.controller._session = self.session
        self.addCleanup(self._stop)
        self.wait_until(lambda: self.session.hello_reply is not None,
                        "the handshake")
        # The first live tick notices the ready worker and announces it.
        self.tick()
        self.wait_until(lambda: self.controller._cached_playback is not None,
                        "the panel to attach Cached Playback")
        self.wait_until(lambda: "frame" in self.socket.types(), "the first live pose")

    def _stop(self):
        session = self.controller._session
        if session is not None:
            session.stop()
        self.flush()

    def _fake_cmds(self):
        fake = mock.MagicMock()
        fake.control.return_value = False
        fake.currentUnit.return_value = "film"
        fake.about.return_value = False
        fake.evalDeferred.side_effect = self.deferred.append
        return fake

    def _fake_om(self):
        harness = self

        def add_timer(interval, callback):
            timer_id = next(harness.timer_ids)
            harness.timers[timer_id] = callback
            return timer_id

        def remove_callback(callback_id):
            harness.timers.pop(callback_id, None)

        return types.SimpleNamespace(
            MSceneMessage=types.SimpleNamespace(
                kBeforeNew=1, kBeforeOpen=2, kMayaExiting=3,
                addCallback=lambda message, callback: next(harness.timer_ids)),
            MEventMessage=types.SimpleNamespace(
                addEventCallback=lambda name, callback: next(harness.timer_ids)),
            MTimerMessage=types.SimpleNamespace(addTimerCallback=add_timer),
            MMessage=types.SimpleNamespace(removeCallback=remove_callback))

    # -------------------------------------------------------------- helpers
    def flush(self):
        """Run the work deferred to Maya's main thread."""
        while self.deferred:
            self.deferred.popleft()()

    def tick(self):
        """Maya's time changes once, as when the animator scrubs."""
        self.session._on_time_changed()
        self.flush()

    def fire_timers(self):
        """Run every Maya timer the tool registered once (session, cache, pump)."""
        for callback in list(self.timers.values()):
            callback()
        self.flush()

    def wait_until(self, predicate, what):
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            self.flush()
            if predicate():
                return
            time.sleep(0.002)
        self.fail("timed out waiting for {0}; sent {1}".format(
            what, self.socket.types()))

    def sent_after(self, count):
        return self.socket.types()[count:]

    def wait_for_sent(self, count, expected, what):
        """Wait until the messages after ``count`` start with ``expected``."""
        self.wait_until(
            lambda: self.sent_after(count)[:len(expected)] == list(expected), what)
        return count + len(expected)

    def mode(self, mode):
        self.controller._on_mode_changed(mode)
        self.flush()

    def enter_linkage_enabled(self):
        """What the panel's 进入联动 button shows now: (enabled, tooltip)."""
        shown = {}
        self.controller._enter_linkage_button = "enterLinkage"
        with mock.patch.object(self.controller, "_set_enabled",
                               lambda control, enabled: shown.update(
                                   {(control, "enabled"): enabled})),                 mock.patch.object(self.controller, "_set_tooltip",
                                  lambda control, tooltip: shown.update(
                                      {(control, "tooltip"): tooltip})):
            self.controller._update_linkage_controls()
        return (shown[("enterLinkage", "enabled")], shown[("enterLinkage", "tooltip")])


class FourClickTests(ConnectionHarness):
    """D-1: 进入联动 → 返回实时 → 缓存播放 → 结束预览, then 实时."""

    def test_end_preview_during_cached_playback_leaves_the_cache_in_control(self):
        control = self.session.time_control
        start = len(self.socket.types())

        self.controller.enter_linkage()
        mark = self.wait_for_sent(start, ["mode"], "the linkage entry")
        self.controller.leave_linkage("realtime")
        mark = self.wait_for_sent(mark, ["leave_linkage", "frame"], "the return to realtime")
        self.mode(mtou_config.CACHED_MODE)
        mark = self.wait_for_sent(mark, ["cache_enter"], "the cached entry")
        self.controller.leave_linkage("end")
        mark = self.wait_for_sent(mark, ["leave_linkage"], "the end of the preview")

        # The cache still drives time: sampling stays paused, delivery ordered.
        self.tick()
        self.assertEqual(mtou_config.CACHED_MODE, control.state)
        self.assertEqual(mtou_config.CACHED_MODE, self.controller._mode)
        self.assertEqual([], self.sent_after(mark))

        self.mode(mtou_config.REALTIME_MODE)
        # The clear reaches Unreal first, then the first live pose.
        self.wait_for_sent(mark, ["cache_clear", "frame"], "the cache clear and live pose")
        self.assertEqual(mtou_config.REALTIME_MODE, control.state)
        self.wait_until(lambda: not control.cache_holds_connection(),
                        "Unreal to confirm the clear")
        self.assertEqual([], self.errors)



class LinkageAfterCachedTests(ConnectionHarness):
    """进入联动 waits for Unreal to confirm the clear that ended Cached Playback."""

    def test_entry_is_refused_during_cached_playback_and_until_the_clear_is_confirmed(self):
        start = len(self.socket.types())
        self.mode(mtou_config.CACHED_MODE)
        mark = self.wait_for_sent(start, ["cache_enter"], "the cached entry")
        enabled, tooltip = self.enter_linkage_enabled()
        self.assertFalse(enabled)
        self.assertIn("返回实时", tooltip)

        self.controller.enter_linkage()
        self.assertEqual("CACHE_NOT_CLEARED", self.errors[-1]["code"])

        self.socket.held_clears = []
        self.mode(mtou_config.REALTIME_MODE)
        mark = self.wait_for_sent(mark, ["cache_clear"], "the cache clear")
        enabled, tooltip = self.enter_linkage_enabled()
        self.assertFalse(enabled)
        self.assertIn("确认清除缓存", tooltip)

        self.controller.enter_linkage()
        self.assertEqual("CACHE_NOT_CLEARED", self.errors[-1]["code"])
        self.assertIsNone(self.controller._linkage)
        # No mode statement reached Unreal for either refused entry.
        self.assertNotIn("mode", self.socket.types())

        self.socket.confirm_clear(self.socket.held_clears.pop())
        self.wait_until(lambda: self.enter_linkage_enabled()[0],
                        "进入联动 to become available")
        self.controller.enter_linkage()
        self.assertEqual(mtou_config.LINKAGE_MODE, self.controller._mode)
        self.wait_until(lambda: "mode" in self.sent_after(mark), "the linkage entry")


ANCHOR = {"location_cm": [0.0, 0.0, 0.0],
          "rotation_deg": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}}


def mode_state(revision, mode="linkage"):
    message = {"type": "mode_state", "session_id": SESSION_ID,
               "mode_revision": revision, "mode": mode,
               "control": "unreal" if mode == "linkage" else "maya", "detail": ""}
    if mode == "linkage":
        message["anchor"] = ANCHOR
    return message


REFERENCE_OFFER = {
    "type": "reference_offer", "session_id": SESSION_ID, "transfer_id": "tx-1",
    "directory": "handoff/tx-1", "manifest_digest": "ab12",
    "scope": {"level": "L_Main"}, "space_revision": 1,
    "counts": {"objects": 1, "triangles": 2, "bytes": 3}}


class UnrealDuringCachedTests(ConnectionHarness):
    """Unreal's statements reach the panel while Cached Playback holds time."""

    def enter_cached(self):
        start = len(self.socket.types())
        self.mode(mtou_config.CACHED_MODE)
        return self.wait_for_sent(start, ["cache_enter"], "the cached entry")

    def test_an_unreal_entry_is_refused_at_once_and_the_cache_keeps_time(self):
        mark = self.enter_cached()
        view = self.controller._cached_playback.view

        self.socket.push(mode_state(1))
        self.wait_for_sent(mark, ["mode"], "Maya's answer to the entry")

        refusal = self.socket.sent[mark]
        self.assertEqual(("realtime", 1, "failed"),
                         (refusal["mode"], refusal["mode_revision"],
                          refusal["transition"]))
        self.assertEqual("CACHE_NOT_CLEARED", self.errors[-1]["code"])
        self.assertIsNone(self.controller._linkage)
        self.assertEqual(mtou_config.CACHED_MODE, self.session.time_control.state)
        self.assertEqual(view.state, self.controller._cached_playback.view.state)
        self.assertNotIn("cache_clear", self.sent_after(mark))

    def test_a_reference_offer_reaches_the_reference_window(self):
        self.enter_cached()

        self.socket.push(REFERENCE_OFFER)
        self.wait_until(lambda: self.controller.reference_offer() is not None,
                        "the reference offer")

        self.assertEqual("tx-1", self.controller.reference_offer()["transfer_id"])


class UnrealErrorTests(ConnectionHarness):
    """An Unreal error has one outcome whatever modes the connection used."""

    ERROR = {"type": "error", "code": "INVALID_MESSAGE", "message": "bad frame",
             "details": "frame 3 has 2 transforms"}

    def use_every_mode(self):
        start = len(self.socket.types())
        self.mode(mtou_config.CACHED_MODE)
        mark = self.wait_for_sent(start, ["cache_enter"], "the cached entry")
        self.mode(mtou_config.REALTIME_MODE)
        mark = self.wait_for_sent(mark, ["cache_clear", "frame"], "the cache clear")
        self.wait_until(lambda: not self.session.time_control.cache_holds_connection(),
                        "Unreal to confirm the clear")
        self.controller.enter_linkage()
        mark = self.wait_for_sent(mark, ["mode"], "the linkage entry")
        self.controller.leave_linkage("realtime")
        mark = self.wait_for_sent(mark, ["leave_linkage", "frame"],
                                  "the return to realtime")
        self.controller.leave_linkage("end")
        self.wait_for_sent(mark, ["leave_linkage"], "the end of the preview")

    def report(self):
        self.socket.push(dict(self.ERROR))
        self.wait_until(lambda: self.errors, "the error to be reported")

    def assert_reported_once_and_kept(self):
        self.report()
        self.fire_timers()
        self.assertEqual([("INVALID_MESSAGE", "frame 3 has 2 transforms")],
                         [(error["code"], error["details"]) for error in self.errors])
        self.assertIs(self.session, self.controller._session)
        self.assertTrue(self.session.is_ready)

    def assert_reported_once_and_ended(self):
        self.report()
        self.socket.hang_up()
        self.wait_until(lambda: (self.fire_timers() or self.controller._session is None),
                        "the connection to end")
        self.assertEqual([("INVALID_MESSAGE", "frame 3 has 2 transforms")],
                         [(error["code"], error["details"]) for error in self.errors])
        self.assertEqual("bad frame", self.controller._last_diagnostic["message"])

    def test_on_a_fresh_connection_it_is_reported_and_the_connection_stays(self):
        self.assert_reported_once_and_kept()

    def test_after_every_mode_it_is_reported_and_the_connection_stays(self):
        self.use_every_mode()
        self.assert_reported_once_and_kept()

    def test_on_a_fresh_connection_unreal_closing_ends_with_that_reason(self):
        self.assert_reported_once_and_ended()

    def test_after_every_mode_unreal_closing_ends_with_that_reason(self):
        self.use_every_mode()
        self.assert_reported_once_and_ended()

    def test_a_cache_error_without_an_identity_is_ignored(self):
        self.socket.push({"type": "error", "code": "CACHE_METADATA_INVALID",
                          "message": "stale clear", "details": "clear_id 1"})
        self.socket.push(REFERENCE_OFFER)
        self.wait_until(lambda: self.controller.reference_offer() is not None,
                        "the message after the error")
        self.assertEqual([], self.errors)
        self.assertTrue(self.session.is_ready)


class DisconnectReasonTests(ConnectionHarness):
    """A connection lost while sampling is paused ends with its real reason."""

    def assert_ends_with(self, code, details=None):
        self.socket.hang_up()
        self.wait_until(lambda: (self.fire_timers() or self.controller._session is None),
                        "the connection to end")
        self.assertEqual(code, self.errors[-1]["code"])
        if details is not None:
            self.assertEqual(details, self.errors[-1]["details"])
        self.assertEqual(1, len(self.errors))

    def test_during_cached_playback(self):
        start = len(self.socket.types())
        self.mode(mtou_config.CACHED_MODE)
        self.wait_for_sent(start, ["cache_enter"], "the cached entry")
        self.assert_ends_with("STREAM_INTERRUPTED")

    def test_during_linkage(self):
        start = len(self.socket.types())
        self.controller.enter_linkage()
        self.wait_for_sent(start, ["mode"], "the linkage entry")
        self.assert_ends_with("STREAM_INTERRUPTED")

    def test_after_an_error_during_cached_playback(self):
        start = len(self.socket.types())
        self.mode(mtou_config.CACHED_MODE)
        self.wait_for_sent(start, ["cache_enter"], "the cached entry")
        self.socket.push({"type": "error", "code": "PAIRING_REQUIRED",
                          "message": "the target was removed",
                          "details": "Binding Actor deleted"})
        self.assert_ends_with("PAIRING_REQUIRED", "Binding Actor deleted")


if __name__ == "__main__":
    unittest.main()
