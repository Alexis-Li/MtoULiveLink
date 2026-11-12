"""Regressions for ordinary-host observations and the shared Maya driver commands."""

import ast
from collections import Counter
from contextlib import contextmanager
import importlib.util
import itertools
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


def closure_value(function, name):
    return dict(zip(function.__code__.co_freevars, function.__closure__))[name].cell_contents


@contextmanager
def editor_driver():
    api = mock.Mock()
    actors = mock.Mock()
    editor = mock.Mock()
    levels = mock.Mock()
    api.get_editor_subsystem.side_effect = {
        api.EditorActorSubsystem: actors, api.UnrealEditorSubsystem: editor,
        api.LevelEditorSubsystem: levels}.__getitem__
    frame = itertools.count()
    api.SystemLibrary.get_frame_count.side_effect = lambda: next(frame)
    api.SystemLibrary.get_engine_version.return_value = "5.7.4"
    api.SystemLibrary.is_valid.side_effect = bool
    actor = actors.spawn_actor_from_object.return_value
    actor.get_path_name.return_value = "/Temp/Untitled.Actor"
    actor.get_components_by_class.return_value = []
    world = [mock.Mock(name="initial_world")]
    editor.get_editor_world.side_effect = lambda: world[0]

    def new_blank_map(save_existing_map):
        world[0] = mock.Mock(name="replacement_world")
        return world[0]

    api.EditorLoadingAndSavingUtils.new_blank_map.side_effect = new_blank_map
    fixture = {"primary_mesh": "/Game/Body", "display_bones": {"/Game/Body": ["pelvis"]}}
    command, published, displays = {}, {}, []

    def read(path):
        if str(path) == "run.json":
            return {"fixture": "fixture.json", "evidence": "evidence"}
        if str(path) == "fixture.json":
            return fixture
        if Path(path).name == "editor-command.json":
            return command
        return {"actors": [{"actor": actor.get_path_name(), "displays": displays}]}

    def write(path, value):
        if Path(path).name == "editor-state.json":
            published.clear()
            published.update(json.loads(json.dumps(value)))

    with mock.patch.dict(sys.modules, {"unreal": api}), mock.patch.dict(
            acceptance.os.environ, {"MTOU_BACKGROUND_RUN": "run.json"}), mock.patch.object(
            acceptance, "read_json", side_effect=read), mock.patch.object(
            acceptance, "write_json", side_effect=write):
        acceptance.editor_main()
        tick = api.register_slate_post_tick_callback.call_args[0][0]
        sequence = itertools.count(1)

        def send(action):
            command.update(id=next(sequence), action=action)
            tick(0.01)
            return published

        yield types.SimpleNamespace(api=api, actors=actors, editor=editor, levels=levels,
                                    actor=actor, world=world, fixture=fixture, displays=displays,
                                    tick=tick, send=send)


def run_pose_oracle(fixture, shown):
    run = next(node for node in ast.parse(TOOL.read_text(encoding="utf-8")).body
               if isinstance(node, ast.FunctionDef) and node.name == "run")
    oracle = next(node for node in ast.walk(run) if isinstance(node, ast.FunctionDef) and node.name == "oracle")
    namespace = dict(vars(acceptance), fixture=fixture, out=Path("unused"),
                     required_meshes=Counter([fixture["primary_mesh"]] + [part["mesh"] for part in fixture.get("parts", [])]),
                     connected={"display_change_hz": dict.fromkeys(shown, 20)},
                     maya=types.SimpleNamespace(pid=42), foreground_pid=lambda: 42,
                     summary={"oracles": []}, write_json=mock.Mock(),
                     maya_box=mock.Mock(), editor_box=mock.Mock())
    namespace["maya_box"].send.return_value = {"snapshot": {"joints": {"pelvis": {"world": [1, 3, 2]}}}}
    namespace["editor_box"].send.return_value = {"pose": shown}
    exec(compile(ast.Module(body=[oracle], type_ignores=[]), str(TOOL), "exec"), namespace)
    namespace["oracle"]("background_connected")
    return namespace["summary"]["oracles"]


def display_pose(mesh, visible=True, registered=True):
    return {"mesh": mesh, "visible": visible, "registered": registered,
            "bones": {"pelvis": [1, 2, 3, 0, 0, 0, 1]}}


def motion_measurement(shown):
    data = {"before": {}, "after": {}, "samples": []}
    for index in range(3):
        poses = json.loads(json.dumps(shown))
        for row in poses.values():
            row["bones"]["pelvis"][0] += index
        data["samples"].append({"seconds": index * 0.05, "frame": index * 3,
                                "foreground_pid": 42, "preference": True, "poses": poses})
    return data


class BackgroundAcceptanceTests(unittest.TestCase):
    def test_close_world_replaces_unsaved_map_repeatedly_in_one_host(self):
        with editor_driver() as driver:
            for unused in range(2):
                driver.send("place")
                previous = driver.world[0]
                state = driver.send("close_world")
                self.assertFalse(state["errors"])
                self.assertIsNot(previous, driver.world[0])
                self.assertIsNone(closure_value(driver.tick, "actor"))
            self.assertEqual([mock.call(False), mock.call(False)],
                             driver.api.EditorLoadingAndSavingUtils.new_blank_map.call_args_list)
            driver.levels.new_level.assert_not_called()

    def test_close_world_failure_keeps_actor_and_does_not_acknowledge_success(self):
        # NewBlankMap returns World or None. Unlike NewLevel it has no bool result.
        for failure in ("none", "unchanged", "different_current_world"):
            with self.subTest(failure=failure), editor_driver() as driver:
                driver.send("place")
                operation = driver.api.EditorLoadingAndSavingUtils.new_blank_map
                operation.side_effect = None
                operation.return_value = {"none": None, "unchanged": driver.world[0],
                                          "different_current_world": mock.Mock(name="unadopted_world")}[failure]
                # The former saved-map operation could fail without replacing the world.
                driver.levels.new_level.return_value = False
                state = driver.send("close_world")
                self.assertEqual(1, state["ack"])
                self.assertRegex("\n".join(state["errors"]), "World replacement failed.*editor log")
                self.assertIs(driver.actor, closure_value(driver.tick, "actor"))

    def test_stable_pose_oracle_rejects_configured_part_missing_from_every_observation(self):
        fixture = {"primary_mesh": "/Game/Body", "parts": [{"name": "Coat", "mesh": "/Game/Coat"}],
                   "oracle_frames": [10], "world_tolerance_cm": 0.1}
        shown = {"Primary": {"mesh": "/Game/Body.Body", "visible": True, "registered": True,
                             "bones": {"pelvis": [1, 2, 3, 0, 0, 0, 1]}}}
        with self.assertRaisesRegex(RuntimeError, "Display mesh counts.*Coat"):
            run_pose_oracle(fixture, shown)

    def test_connected_measurement_rejects_missing_part_even_when_all_observations_agree(self):
        fixture = {"primary_mesh": "/Game/Body", "parts": [{"name": "Coat", "mesh": "/Game/Coat"}]}
        data = motion_measurement({"Primary": display_pose("/Game/Body.Body")})
        # Both old cadence inputs could agree on just Primary and pass.
        old_summary = acceptance.summarize(data, 42)
        old_summary["producer_hz"] = 20
        limits = {"connected_min_hz": 20, "reference_ratio_min": 0.9,
                  "producer_min_hz": 5, "pose_ratio_min": 0.9}
        acceptance.check_connected_cadence(old_summary, old_summary, {"frame_hz": 3}, limits)
        with self.assertRaisesRegex(RuntimeError, "Display mesh counts.*Coat"):
            acceptance.summarize(data, 42, acceptance.required_display_meshes(fixture))

    def test_connected_measurement_and_oracle_require_visibility_and_registration(self):
        fixture = {"primary_mesh": "/Game/Body", "oracle_frames": [10], "world_tolerance_cm": 0.1}
        required = acceptance.required_display_meshes(fixture)
        for field in ("visible", "registered"):
            with self.subTest(field=field):
                shown = {"Primary": display_pose("/Game/Body.Body")}
                data = motion_measurement(shown)
                data["samples"][1]["poses"]["Primary"][field] = False
                with self.assertRaisesRegex(RuntimeError, "must be visible and registered"):
                    acceptance.summarize(data, 42, required)
                shown["Primary"][field] = False
                with self.assertRaisesRegex(RuntimeError, "must be visible and registered"):
                    run_pose_oracle(fixture, shown)

    def test_fixture_mesh_identities_and_duplicate_part_counts_apply_to_both_oracles(self):
        fixture = {"primary_mesh": "/Game/Body", "parts": [
            {"name": "Left", "mesh": "/Game/Coat"}, {"name": "Right", "mesh": "/Game/Coat"}],
            "oracle_frames": [10], "world_tolerance_cm": 0.1}
        required = acceptance.required_display_meshes(fixture)
        self.assertEqual(Counter({"/Game/Body": 1, "/Game/Coat": 2}), required)
        for coat_meshes in (("/Game/Coat.Coat",), ("/Game/Coat.Coat", "/Game/Other.Other")):
            with self.subTest(coat_meshes=coat_meshes):
                shown = {"Primary": display_pose("/Game/Body.Body")}
                shown.update({"Part%s" % index: display_pose(mesh) for index, mesh in enumerate(coat_meshes)})
                with self.assertRaisesRegex(RuntimeError, "Display mesh counts"):
                    acceptance.summarize(motion_measurement(shown), 42, required)
                with self.assertRaisesRegex(RuntimeError, "Display mesh counts"):
                    run_pose_oracle(fixture, shown)

    def test_single_primary_and_complete_repeated_mesh_composition_pass_both_oracles(self):
        for part_count in (0, 2):
            with self.subTest(part_count=part_count):
                fixture = {"primary_mesh": "/Game/Body", "parts": [
                    {"name": "Part%s" % index, "mesh": "/Game/Coat"} for index in range(part_count)],
                    "oracle_frames": [10], "world_tolerance_cm": 0.1}
                shown = {"Primary": display_pose("/Game/Body.Body")}
                shown.update({"Part%s" % index: display_pose("/Game/Coat.Coat") for index in range(part_count)})
                value = acceptance.summarize(motion_measurement(shown), 42, acceptance.required_display_meshes(fixture))
                self.assertEqual(set(shown), set(value["display_change_hz"]))
                self.assertEqual(0, run_pose_oracle(fixture, shown)[0]["observations"][0]["max_world_error_cm"])

    def test_poses_preserves_mesh_and_bad_display_status_before_reading_bones(self):
        with editor_driver() as driver:
            components = []
            for name, visible, registered in (("Primary", True, True), ("Invisible", False, True),
                                               ("Unregistered", True, False)):
                component = mock.Mock()
                component.get_name.return_value = name
                component.get_skeletal_mesh_asset.return_value.get_path_name.return_value = "/Game/Body.Body"
                component.get_bone_index.return_value = 0
                component.get_bone_transform.return_value = types.SimpleNamespace(
                    translation=types.SimpleNamespace(x=1, y=2, z=3),
                    rotation=types.SimpleNamespace(x=0, y=0, z=0, w=1))
                components.append(component)
                driver.displays.append({"component": name, "visible": visible, "registered": registered})
            driver.actor.get_components_by_class.return_value = components
            state = driver.send("place")
            self.assertFalse(state["errors"])
            shown = state["displays"]
            self.assertEqual({"Primary", "Invisible", "Unregistered"}, set(shown))
            self.assertFalse(shown["Invisible"]["visible"])
            self.assertFalse(shown["Unregistered"]["registered"])
            for component in components[1:]:
                component.get_bone_transform.assert_not_called()
            self.assertEqual("/Game/Body.Body", shown["Unregistered"]["mesh"])
            self.assertTrue(shown["Primary"]["registered"])
            self.assertIn("pelvis", shown["Primary"]["bones"])

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
