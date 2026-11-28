# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Headless rules for settings, diagnostics and Qt isolation."""

import ast
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from mtou_livelink.controller import Controller
from mtou_livelink.config import CACHED_MODE, CONTROL_MAYA, CONTROL_UNREAL
from mtou_livelink.diagnostics import linkage_diagnostic_text, make_diagnostic
from mtou_livelink.preferences import ScenePreferences, SceneSettings
from mtou_livelink.session import StreamingSessionEvent
from mtou_livelink.timecontrol import TimeControl


class PanelRulesTests(unittest.TestCase):
    def test_preferences_round_trip_by_normalized_path(self):
        defaults = SceneSettings()
        with tempfile.TemporaryDirectory() as user_preferences:
            prefs = ScenePreferences(user_preferences)
            first = str(Path(user_preferences) / "first.ma")
            second = str(Path(user_preferences) / "second.ma")
            alias = str(Path(user_preferences) / "nested" / ".." / "first.ma")
            saved = SceneSettings(workflow="model", blendshapes_enabled=False,
                                  show_popups=False, playback_cap=24, custom_range=True,
                                  range_start=-10, range_end=70, root="|主角|root",
                                  secondary_root="|道具|root", display="|主角|Display",
                                  display_attribute="clothes")
            prefs.save(first, saved)
            prefs.save(second, SceneSettings(playback_cap=30))
            self.assertEqual(saved, prefs.load(alias, defaults))
            self.assertEqual(30, prefs.load(second, defaults).playback_cap)
            self.assertEqual(defaults, prefs.load("", defaults))
            self.assertFalse(Path(first).exists())
            self.assertFalse(Path(second).exists())

    def test_corrupt_preferences_are_limited_to_one_scene_and_can_be_replaced(self):
        with tempfile.TemporaryDirectory() as user_preferences:
            prefs = ScenePreferences(user_preferences)
            defaults = SceneSettings()
            settings = SceneSettings(show_popups=False)
            prefs.save("one.ma", settings)
            prefs.save("two.ma", settings)
            prefs.path_for("one.ma").write_bytes(b'{"version":')
            with self.assertRaises(ValueError):
                prefs.load("one.ma", defaults)
            self.assertEqual(settings, prefs.load("two.ma", defaults))
            prefs.save("one.ma", defaults)
            self.assertEqual(defaults, prefs.load("one.ma", settings))
            self.assertEqual(settings, prefs.load("two.ma", defaults))

    def test_failed_write_keeps_current_settings_and_a_visible_warning(self):
        operations = Controller()
        operations._scene_path = "character.ma"
        operations._preferences = mock.Mock()
        operations._preferences.save.side_effect = OSError("directory unavailable")
        operations._save_settings(operation="role", root="|Character|root")
        state = operations.snapshot()
        self.assertEqual("|Character|root", state["settings"].root)
        self.assertEqual("PREFERENCES_WRITE_FAILED", state["diagnostic"]["code"])
        self.assertEqual("preferences_save", state["diagnostic"]["operation"])
        self.assertTrue(state["diagnostic"]["solution"])

    def test_new_failure_keeps_earlier_difference_and_fences_late_results(self):
        operations = Controller()
        received = []
        operations.subscribe(received.append)
        operations._interact = mock.Mock()
        operations._scene = SimpleNamespace(snapshot=lambda: SimpleNamespace(
            root="|root", outfit="Clothes01", bones=[], curve_names=[],
            duplicate_paths=[], mesh_paths=[]))
        operations.set_workflow("model")
        operations.set_popups(False)
        session = SimpleNamespace(is_ready=True, hello_reply={"session_id": 4, "version": 12})
        operations._session = session
        operations._diagnostics.begin("connect")
        operations._pending_connect_attempt = operations._diagnostics.stamp
        warning = {"has_warning": True, "missing_in_unreal": ["Smile"],
                   "missing_in_maya": [], "bone_name_remaps": []}
        ready = StreamingSessionEvent("ready", warning=warning)
        operations._on_streaming_session_event(session, ready)
        self.assertEqual("BLENDSHAPE_MISMATCH", received[-1]["diagnostic"]["code"])
        deferred = []
        operations._defer_ui = deferred.append
        operations._report_runtime_error(
            {"type": "error", "code": "STREAM_INTERRUPTED", "details": "old frame"})
        failure = make_diagnostic("PREVIEW_BUILD_FAILED", details="latest operation")
        operations._show_error(failure, operation="linkage")
        deferred.pop()()
        operations._on_streaming_session_event(session, ready)
        after = received[-1]
        self.assertEqual("PREVIEW_BUILD_FAILED", after["diagnostic"]["code"])
        self.assertEqual("latest operation", after["diagnostic"]["details"])
        self.assertIn("Smile", after["connection_difference"]["details"])
        self.assertTrue(after["diagnostic"]["solution"])

    def test_realtime_disconnect_with_an_attached_cache_keeps_the_realtime_failure(self):
        from mtou_livelink.cache import CachedPlayback
        from mtou_livelink.cache import CachedPlaybackView

        operations = Controller()
        operations.set_popups(False)
        operations._session = session = SimpleNamespace(is_ready=True)
        operations._connection = "connected"
        failure = make_diagnostic("INVALID_BINDING", details="Live Binding removed")
        operations._report_runtime_error(
            {"type": "error", "code": failure["code"], "details": failure["details"]})

        cached = mock.Mock()
        cached.view = CachedPlaybackView(CachedPlayback.DETACHED, diagnostic=failure)

        def detach(event):
            operations._on_cached_playback_view(cached.view)

        cached.detach.side_effect = detach
        operations._cached_playback = cached
        operations._on_streaming_session_event(
            session, StreamingSessionEvent("failed", diagnostic=failure))
        state = operations.snapshot()
        self.assertEqual("interrupted", state["connection"])
        self.assertEqual("INVALID_BINDING", state["diagnostic"]["code"])
        self.assertEqual("preview", state["diagnostic"]["operation"])
        self.assertEqual("Live Binding removed", state["diagnostic"]["details"])

    def test_terminal_realtime_error_keeps_its_origin_before_deferred_delivery(self):
        from mtou_livelink.session import SenderWorker, StreamingSession
        operations = Controller()
        operations._connection = "connected"
        session = StreamingSession(None, 24, None)
        session._worker = worker = SenderWorker(None)
        session._on_statement = lambda reply: operations._on_session_statement(
            {"session": session}, reply)
        operations._session = session
        deferred = []
        operations._defer_ui = deferred.append
        operations._interact = popup = mock.Mock()
        session._route_reply({"type": "error", "kind": "failure", "code": "INVALID_BINDING",
                              "details": "Live Binding removed"})
        failure = worker._close_reason()
        operations._on_streaming_session_event(
            session, StreamingSessionEvent("failed", diagnostic=failure))
        deferred.pop()()
        result = operations.snapshot()["diagnostic"]
        self.assertEqual("preview", result["operation"])
        self.assertEqual("INVALID_BINDING", result["code"])
        self.assertEqual("Live Binding removed", result["details"])
        self.assertEqual(1, popup.call_count)

    def test_repeated_runtime_errors_refresh_diagnostics_even_when_eof_arrives_first(self):
        from mtou_livelink.session import SenderWorker, StreamingSession

        for eof_first in (False, True):
            with self.subTest(eof_first=eof_first):
                operations = Controller()
                operations._connection = "connected"
                operations._session = session = StreamingSession(None, 24, None)
                session._worker = worker = SenderWorker(None)
                holder = {"session": session}
                session._on_statement = lambda reply: operations._on_session_statement(holder, reply)
                deferred = []
                operations._defer_ui = deferred.append
                operations._interact = popup = mock.Mock()
                first = {"type": "error", "code": "INVALID_BINDING", "details": "invalid target"}
                second = {"type": "error", "code": "MODE_CONFLICT", "details": "mode switched"}
                for reply in (first, second):
                    session._route_reply(reply)
                    deferred.pop(0)()
                self.assertEqual("MODE_CONFLICT", operations.snapshot()["diagnostic"]["code"])

                session._route_reply(first)
                finish = StreamingSessionEvent("failed", diagnostic=worker._close_reason())
                if not eof_first:
                    deferred.pop(0)()
                    self.assertEqual("INVALID_BINDING", operations.snapshot()["diagnostic"]["code"])
                operations._on_streaming_session_event(session, finish)
                if eof_first:
                    deferred.pop(0)()
                result = operations.snapshot()["diagnostic"]
                self.assertEqual("INVALID_BINDING", result["code"])
                self.assertEqual("preview", result["operation"])
                self.assertEqual(first["details"], result["details"])
                self.assertEqual(2, popup.call_count)

    def test_terminal_runtime_error_does_not_replace_a_newer_operation(self):
        from mtou_livelink.session import SenderWorker, StreamingSession

        operations = Controller()
        operations._connection = "connected"
        operations._session = session = StreamingSession(None, 24, None)
        session._worker = worker = SenderWorker(None)
        holder = {"session": session}
        session._on_statement = lambda reply: operations._on_session_statement(holder, reply)
        deferred = []
        operations._defer_ui = deferred.append
        operations._interact = popup = mock.Mock()
        session._route_reply({"type": "error", "code": "INVALID_BINDING", "details": "old target"})
        operations._show_error(make_diagnostic("PREVIEW_BUILD_FAILED"), operation="linkage")
        operations._on_streaming_session_event(
            session, StreamingSessionEvent("failed", diagnostic=worker._close_reason()))
        deferred.pop(0)()
        self.assertEqual("interrupted", operations.snapshot()["connection"])
        self.assertEqual("PREVIEW_BUILD_FAILED", operations.snapshot()["diagnostic"]["code"])
        self.assertEqual(1, popup.call_count)

    def test_single_character_cache_shares_unreal_authority_with_full_diagnostics(self):
        operations = Controller()
        operations._session = SimpleNamespace(time_control=TimeControl())
        state = operations.snapshot()
        self.assertEqual(CONTROL_MAYA, state["authority"])
        self.assertEqual(state["authority"], state["linkage"]["control"])
        operations._session.time_control.acquire(CACHED_MODE, self)
        state = operations.snapshot()
        self.assertEqual(CACHED_MODE, state["mode"])
        self.assertEqual(CONTROL_UNREAL, state["authority"])
        self.assertEqual(state["authority"], state["linkage"]["control"])
        self.assertIn("控制方：Unreal", linkage_diagnostic_text(state["linkage"]))

    def test_controller_and_actual_entry_import_without_maya_or_qt_work(self):
        script = """
import sys
from types import ModuleType
maya = ModuleType("maya")
maya.__path__ = []
cmds = ModuleType("maya.cmds")
def fail(*args, **kwargs):
    raise AssertionError("Maya command during import")
cmds.about = fail
maya.cmds = cmds
api = ModuleType("maya.api")
api.__path__ = []
maya.api = api
api.OpenMaya = ModuleType("maya.api.OpenMaya")
api.OpenMayaAnim = ModuleType("maya.api.OpenMayaAnim")
sys.modules.update({"maya": maya, "maya.cmds": cmds, "maya.api": api,
                    "maya.api.OpenMaya": api.OpenMaya, "maya.api.OpenMayaAnim": api.OpenMayaAnim})
sys.path.insert(0, sys.argv[1])
import MtoULiveLink
assert callable(MtoULiveLink.run)
assert not any(name.startswith(("PySide", "shiboken")) for name in sys.modules)
"""
        process = subprocess.run([sys.executable, "-I", "-c", script, str(SCRIPTS)],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(0, process.returncode, process.stderr)

    def test_qt_bindings_and_panel_strings_have_single_owners(self):
        package = SCRIPTS / "mtou_livelink"
        qt_modules = {"PySide2", "PySide6", "shiboken2", "shiboken6"}
        for path in package.rglob("*.py"):
            if path.name in {"qt_compat.py", "strings.py", "install.py"}:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = {node.body[0].value for node in ast.walk(tree)
                          if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
                          and node.body and isinstance(node.body[0], ast.Expr)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    direct = {item.name.partition(".")[0] for item in node.names}
                    self.assertFalse(direct & qt_modules, path)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.partition(".")[0], qt_modules, path)
                elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                      and node not in docstrings):
                    self.assertFalse(any("\u3400" <= char <= "\u9fff" for char in node.value),
                                     "{0}:{1}".format(path, node.lineno))
