"""Regressions for ordinary-host observations and the shared Maya driver commands."""

import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "background_updates_acceptance.py"
DRIVER = ROOT / "maya" / "MtoULiveLink" / "tests" / "maya_gui_linkage_driver.py"
SPEC = importlib.util.spec_from_file_location("background_updates_acceptance", TOOL)
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


def snapshot(connected=False, background=True):
    return {"automation_testing": False, "unattended": False,
            "foreground": not background, "has_focus": not background,
            "throttle_cpu_when_not_foreground": True, "connected": connected,
            "owner_cpu_delegate_count": int(connected),
            "should_throttle_cpu_usage": background and not connected,
            "viewports": [{"realtime": connected, "base_realtime": False}]}


class BackgroundAcceptanceTests(unittest.TestCase):
    def test_localized_main_window_ignores_untitled_helper_and_smaller_windows(self):
        windows = [(101, "", 4000000), (102, "BackgroundHost - 虚幻编辑器", 2000000),
                   (103, "Output", 80000)]
        self.assertEqual(102, acceptance.largest_titled_window(windows, 42))
        with self.assertRaisesRegex(RuntimeError, "Cannot identify"):
            acceptance.largest_titled_window([(1, "A", 10), (2, "B", 10)], 42)

    def test_launcher_has_no_focus_or_input_mutation_calls(self):
        forbidden = {"SetForegroundWindow", "ShowWindow", "SetActiveWindow", "SwitchDesktop",
                     "keybd_event", "mouse_event", "SendInput", "AttachThreadInput"}
        calls = {node.func.attr for node in ast.walk(ast.parse(TOOL.read_text(encoding="utf-8")))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(calls & forbidden)

    def test_startup_requests_nonactivating_window_and_records_owned_process(self):
        fake_subprocess = types.SimpleNamespace(
            STARTUPINFO=lambda: types.SimpleNamespace(), STARTF_USESHOWWINDOW=1,
            CREATE_NO_WINDOW=0x08000000, STDOUT=-2, Popen=mock.Mock())
        owned = []
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(acceptance, "subprocess", fake_subprocess):
            process = acceptance.start_host(["host"], {"test": "env"}, Path(directory) / "log", owned)
        options = fake_subprocess.Popen.call_args[1]
        self.assertEqual(4, options["startupinfo"].wShowWindow)
        self.assertEqual(0x81, options["startupinfo"].dwFlags)
        self.assertEqual([process], owned)

    def test_snapshot_requires_real_engine_focus_and_owned_recovery(self):
        acceptance.check_snapshot(snapshot(), False, True)
        acceptance.check_snapshot(snapshot(True), True, True)
        acceptance.check_snapshot(snapshot(True, False), True, False)
        for field, bad in (("foreground", True), ("has_focus", True),
                           ("automation_testing", True), ("unattended", True),
                           ("throttle_cpu_when_not_foreground", False),
                           ("owner_cpu_delegate_count", 1)):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                acceptance.check_snapshot(dict(snapshot(), **{field: bad}), False, True)
        bad = snapshot()
        bad["viewports"][0]["base_realtime"] = True
        acceptance.check_snapshot(bad, False, True)
        bad["viewports"][0].pop("base_realtime")
        with self.assertRaisesRegex(RuntimeError, "not observed"):
            acceptance.check_snapshot(bad, False, True)

    def test_summary_observes_actual_duration_pose_changes_and_foreground_loss(self):
        data = {"before": {}, "after": {}, "samples": []}
        for index in range(3):
            data["samples"].append({"seconds": 10 + index * 0.5, "frame": 100 + index * 5,
                                    "foreground_pid": 42 if index != 1 else 99,
                                    "preference": True,
                                    "poses": {"body": {"bones": {"pelvis": [index]}}}})
        value = acceptance.summarize(data, 42)
        self.assertEqual(1, value["duration_seconds"])
        self.assertEqual(10, value["frame_hz"])
        self.assertEqual({"body": 2}, value["display_change_hz"])
        self.assertAlmostEqual(2 / 3, value["foreground_fraction"])
        data["samples"][1]["frame"] = 100
        with self.assertRaisesRegex(RuntimeError, "deduplicated"):
            acceptance.summarize(data, 42)

    def test_fixture_requires_justified_limits_and_motion_oracles(self):
        fixture = json.loads((ROOT / "docs" / "acceptance-fixtures" / "background-updates.json").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(RuntimeError, "rationale"):
            acceptance.validate_fixture(fixture)
        # These numbers exercise validation only. Every host run declares its own limits.
        fixture["thresholds"] = {"rationale": "Unit-test input",
                                  "connected_min_hz": 20, "baseline_max_hz": 4,
                                  "producer_min_hz": 5, "reference_ratio_min": 0.9,
                                  "recovery_ratio_max": 1.1, "pose_ratio_min": 0.9}
        acceptance.validate_fixture(fixture)
        fixture["motion_frames"] = [4, 4]
        with self.assertRaisesRegex(RuntimeError, "motion_frames"):
            acceptance.validate_fixture(fixture)

    def test_cadence_rejects_frozen_display_and_reference_rate_loss(self):
        limits = {"connected_min_hz": 20, "reference_ratio_min": 0.9,
                  "producer_min_hz": 5, "pose_ratio_min": 0.9}
        reference = {"frame_hz": 60, "producer_hz": 20, "display_change_hz": {"body": 19}}
        baseline = {"frame_hz": 3}
        acceptance.check_connected_cadence(reference, reference, baseline, limits)
        for change in ({"frame_hz": 30}, {"display_change_hz": {"body": 0}}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                acceptance.check_connected_cadence(dict(reference, **change), reference, baseline, limits)

    def test_startup_driver_errors_fail_immediately(self):
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch.object(acceptance, "read_json", return_value={"errors": ["missing mesh"]}):
            with self.assertRaisesRegex(RuntimeError, "missing mesh"):
                acceptance.host_state("state.json", process)

    def test_atomic_mailbox_write_handles_windows_reader_sharing_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "command.json"
            original_replace = Path.replace
            attempts = []

            def replace(source, destination):
                attempts.append(source)
                if len(attempts) == 1:
                    raise PermissionError("destination is still open in the host reader")
                return original_replace(source, destination)

            with mock.patch.object(Path, "replace", replace), mock.patch.object(acceptance.time, "sleep"):
                acceptance.write_json(path, {"id": 1})
            self.assertEqual({"id": 1}, acceptance.read_json(path))
            self.assertEqual(2, len(attempts))

    def test_maya_quit_can_complete_before_command_ack(self):
        process = mock.Mock()
        process.poll.return_value = 0
        mailbox = acceptance.Mailbox("command", "result", process)
        with mock.patch.object(acceptance, "write_json"), mock.patch.object(
                acceptance, "read_json", return_value={"ack": 0, "phase": "quit"}):
            self.assertEqual("quit", mailbox.send("quit", allow_exit=True)["phase"])
            with self.assertRaisesRegex(RuntimeError, "before acknowledging"):
                mailbox.send("time")


def driver_functions():
    tree = ast.parse(DRIVER.read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ("connect_role", "run_command", "start")]
    namespace = {"FIXTURE": {"root": "root"}, "CONTROLLER": mock.Mock(),
                 "cmds": mock.Mock(), "RESULT": {}, "time": time,
                 "select_secondary": mock.Mock()}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(DRIVER), "exec"), namespace)
    return namespace


class MayaDriverCommandTests(unittest.TestCase):
    def test_existing_fixture_still_connects_and_disconnected_role_does_not(self):
        state = driver_functions()
        state["connect_role"]()
        state["CONTROLLER"].connect.assert_called_once()
        state["CONTROLLER"].reset_mock()
        state["FIXTURE"]["secondary"] = {"root": "secondary"}
        state["connect_role"](False)
        state["CONTROLLER"].set_role.assert_called_once()
        state["CONTROLLER"].set_secondary_object.assert_called_once()
        state["CONTROLLER"].connect.assert_not_called()

    def test_evaluated_time_ack_follows_synchronous_time_change(self):
        state = driver_functions()
        state["cmds"].currentTime.return_value = 123.0
        state["run_command"]({"action": "time", "frame": 123})
        self.assertEqual([mock.call(123.0, update=True), mock.call(query=True)],
                         state["cmds"].currentTime.call_args_list)
        self.assertEqual(123.0, state["RESULT"]["time_evaluated"]["frame"])
        self.assertGreater(state["RESULT"]["time_evaluated"]["seconds"], 0)

    def test_fatal_packet_uses_the_existing_session_sender(self):
        state = driver_functions()
        state["run_command"]({"action": "fatal_protocol"})
        state["CONTROLLER"]._session._worker.submit_control.assert_called_once_with(
            {"type": "frame", "transforms": None, "curves": []})

    def test_start_disconnected_is_opt_in(self):
        for disconnected in (False, True):
            with self.subTest(disconnected=disconnected):
                state = driver_functions()
                state.update({name: mock.Mock() for name in (
                    "write_result", "open_scene", "key_test_motion", "key_test_morphs",
                    "install_callback_census", "connect_role", "fail", "tick")})
                state["FIXTURE"]["start_disconnected"] = disconnected
                state["TIMER"] = [None]
                qt_core = types.ModuleType("PySide2.QtCore")
                qt_core.QTimer = mock.Mock()
                entry = types.ModuleType("MtoULiveLink")
                entry.run = mock.Mock(return_value=state["CONTROLLER"])
                with mock.patch.dict(sys.modules, {"MtoULiveLink": entry, "PySide2.QtCore": qt_core}):
                    state["start"]()
                state["fail"].assert_not_called()
                state["connect_role"].assert_called_once_with(connect=not disconnected)


if __name__ == "__main__":
    unittest.main()
