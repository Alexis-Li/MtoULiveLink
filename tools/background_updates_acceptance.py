"""Measure connection-scoped updates in an ordinary, rendered Unreal Editor.

Run with a system Python and --help. The same file is the editor startup
script. Only task-owned processes and transient actors are changed.
"""

from __future__ import annotations

import argparse
from collections import Counter
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    # The real Maya/Editor reader can hold the destination open on Windows.
    # Match the existing GUI harness's bounded sharing-conflict retry.
    for attempt in range(50):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


def win32():
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetForegroundWindow.restype = wintypes.HWND
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    for name in ("IsIconic", "IsWindowVisible", "GetWindowTextLengthW"):
        getattr(api, name).argtypes = [wintypes.HWND]
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    return api


def foreground_pid():
    api = win32()
    pid = ctypes.c_ulong()
    api.GetWindowThreadProcessId(api.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value


def process_window(pid):
    api = win32()
    windows = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def collect(window, unused):
        owner = ctypes.c_ulong()
        api.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == pid and api.IsWindowVisible(window):
            length = api.GetWindowTextLengthW(ctypes.c_void_p(window))
            title = ctypes.create_unicode_buffer(length + 1)
            api.GetWindowTextW(ctypes.c_void_p(window), title, length + 1)
            rectangle = wintypes.RECT()
            api.GetWindowRect(ctypes.c_void_p(window), ctypes.byref(rectangle))
            area = (rectangle.right - rectangle.left) * (rectangle.bottom - rectangle.top)
            windows.append((window, title.value, area))
        return True

    api.EnumWindows(collect, 0)
    return largest_titled_window(windows, pid)


def largest_titled_window(windows, pid):
    candidates = sorted((item for item in windows if item[1] and item[2] > 0),
                        key=lambda item: item[2], reverse=True)
    if not candidates or (len(candidates) > 1 and candidates[0][2] == candidates[1][2]):
        raise RuntimeError("Cannot identify the largest titled window for PID %s, got %r" % (pid, windows))
    return candidates[0][0]


def start_host(command, env, log, owned_processes):
    # Match the existing GUI harness's non-activating launch intent. Hosts can
    # create more windows later, so actual foreground state is always observed.
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW | 0x80  # STARTF_FORCEOFFFEEDBACK
    startup.wShowWindow = 4  # SW_SHOWNOACTIVATE, normal size without activation
    with Path(log).open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(command, env=env, startupinfo=startup,
                                   creationflags=subprocess.CREATE_NO_WINDOW,
                                   stdout=stream, stderr=subprocess.STDOUT)
    owned_processes.append(process)
    return process


def host_state(path, process):
    result = read_json(path)
    if result.get("errors") or result.get("ok") is False:
        raise RuntimeError("Host driver failed " + str(result.get("errors")))
    if process.poll() is not None:
        raise RuntimeError("Host exited before reaching the required state")
    return result


def wait_for(predicate, description, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    raise RuntimeError("Timed out waiting for " + description)


def editor_main():
    import unreal

    config = read_json(os.environ["MTOU_BACKGROUND_RUN"])
    out = Path(config["evidence"])
    fixture = read_json(config["fixture"])
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    settings = unreal.get_default_object(unreal.load_class(None, "/Script/UnrealEd.EditorPerformanceSettings"))
    state = {"ack": 0, "pid": os.getpid(), "engine": unreal.SystemLibrary.get_engine_version(),
             "errors": [], "stage": "startup", "samples": [], "last_frame": -1}
    binding = unreal.new_object(unreal.load_class(None, "/Script/MtoULiveLink.MtoULiveLinkBinding"))
    binding.set_editor_property("skeletal_mesh", unreal.load_asset(fixture["primary_mesh"]))
    parts = []
    for spec in fixture.get("parts", []):
        part = unreal.MtoUCharacterPart()
        part.set_editor_property("part_name", spec["name"])
        part.set_editor_property("skeletal_mesh", unreal.load_asset(spec["mesh"]))
        part.set_editor_property("enabled", True)
        parts.append(part)
    binding.set_editor_property("additional_parts", parts)
    actor = None

    def place_actor():
        return actors.spawn_actor_from_object(binding, unreal.Vector(), transient=True)

    def poses():
        result = {}
        if not actor or not unreal.SystemLibrary.is_valid(actor):
            return result
        observed = snapshot("display-status")
        display_states = {row["component"]: row for entry in observed.get("actors", [])
                          if entry["actor"] == actor.get_path_name() for row in entry["displays"]}
        for component in actor.get_components_by_class(unreal.SkeletalMeshComponent):
            mesh = component.get_skeletal_mesh_asset()
            if not mesh:
                continue
            status = display_states.get(component.get_name(), {})
            row = {"mesh": mesh.get_path_name(), "visible": status.get("visible"),
                   "registered": status.get("registered"), "bones": {}}
            result[component.get_name()] = row
            if row["visible"] is not True or row["registered"] is not True:
                continue
            names = fixture["display_bones"].get(mesh.get_path_name().split(".")[0], [])
            bones = {}
            for bone in names:
                if component.get_bone_index(bone) < 0:
                    raise RuntimeError("Missing oracle bone %s on %s" % (bone, component.get_name()))
                transform = component.get_bone_transform(bone, unreal.RelativeTransformSpace.RTS_WORLD)
                p, q = transform.translation, transform.rotation
                bones[bone] = [p.x, p.y, p.z, q.x, q.y, q.z, q.w]
            if not bones:
                raise RuntimeError("No oracle bones configured for visible display " + mesh.get_path_name())
            row["bones"] = bones
        return result

    def snapshot(name):
        path = out / (name + "-snapshot.json")
        unreal.SystemLibrary.execute_console_command(
            unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world(),
            'MtoU.BackgroundSnapshot "%s"' % path.as_posix())
        return read_json(path)

    def publish():
        write_json(out / "editor-state.json", {key: value for key, value in state.items() if key != "samples"})

    def tick(delta):
        nonlocal actor
        try:
            frame = unreal.SystemLibrary.get_frame_count()
            if frame == state["last_frame"]:
                return
            state["last_frame"] = frame
            command = read_json(out / "editor-command.json")
            if command.get("id", 0) > state["ack"]:
                action = command["action"]
                if action == "place":
                    actor = place_actor()
                    state["actor"] = actor.get_path_name()
                    state["displays"] = poses()
                elif action == "begin":
                    state["stage"] = command["name"]
                    state["samples"] = []
                    state["before"] = snapshot(command["name"] + "-before")
                elif action == "end":
                    result = {"stage": state["stage"], "before": state["before"],
                              "after": snapshot(state["stage"] + "-after"), "samples": state["samples"]}
                    write_json(out / (state["stage"] + ".json"), result)
                    state["samples"] = []
                    state["stage"] = "idle"
                elif action == "pose":
                    state["pose"] = poses()
                elif action == "destroy_actor":
                    actors.destroy_actor(actor)
                    actor = None
                elif action == "close_world":
                    editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
                    previous_world = editor.get_editor_world()
                    replacement = unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
                    if not replacement or not unreal.SystemLibrary.is_valid(replacement):
                        raise RuntimeError("World replacement failed because new_blank_map(False) returned no valid World. "
                                           "actor reference retained. Inspect the editor log before retrying in the disposable host.")
                    if replacement == previous_world or editor.get_editor_world() != replacement:
                        raise RuntimeError("World replacement failed because new_blank_map(False) did not install a different editor World. "
                                           "actor reference retained. Inspect the editor log before retrying in the disposable host.")
                    actor = None
                elif action == "remove_source":
                    # An empty preset's public Apply operation removes the
                    # isolated editor's sources through the regular client.
                    preset = unreal.LiveLinkPreset()
                    if not preset.apply_to_client():
                        raise RuntimeError("Empty Live Link preset failed to apply")
                elif action == "snapshot":
                    state["snapshot"] = snapshot(command["name"])
                elif action == "quit":
                    state["shutdown"] = snapshot("shutdown")
                    state["ack"] = command["id"]
                    publish()
                    unreal.unregister_slate_post_tick_callback(handle)
                    unreal.EditorPythonScripting.set_keep_python_script_alive(False)
                    return
                else:
                    raise RuntimeError("Unknown editor action " + action)
                state["ack"] = command["id"]
                publish()
            if state["stage"] not in ("startup", "idle"):
                state["samples"].append({"seconds": time.perf_counter(), "frame": frame,
                                         "foreground_pid": foreground_pid(), "poses": poses(),
                                         "preference": bool(settings.get_editor_property("bThrottleCPUWhenNotForeground"))})
        except Exception:
            state["errors"].append(traceback.format_exc())
            publish()
            unreal.unregister_slate_post_tick_callback(handle)
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)

    unreal.EditorPythonScripting.set_keep_python_script_alive(True)
    handle = unreal.register_slate_post_tick_callback(tick)
    state["ready"] = True
    publish()


class Mailbox:
    def __init__(self, command, result, process):
        self.command = command
        self.result = result
        self.process = process
        self.sequence = 0

    def send(self, action, allow_exit=False, **fields):
        self.sequence += 1
        write_json(self.command, dict(fields, id=self.sequence, action=action))

        def acknowledged():
            result = read_json(self.result)
            if result.get("errors") or result.get("ok") is False:
                raise RuntimeError(str(result["errors"]))
            if result.get("ack", -1) == self.sequence:
                return result
            if self.process.poll() is not None:
                if allow_exit and action == "quit" and result.get("phase") == "quit":
                    return result
                raise RuntimeError("Process exited before acknowledging " + action)
            return None

        return wait_for(acknowledged, action + " acknowledgement")


def required_display_meshes(fixture):
    # Every configured part is enabled by editor_main. Count repeated meshes.
    return Counter(mesh.split(".")[0] for mesh in
                   [fixture["primary_mesh"]] + [part["mesh"] for part in fixture.get("parts", [])])


def check_displays(shown, required_meshes):
    observed = Counter(row["mesh"].split(".")[0] for row in shown.values())
    if observed != required_meshes:
        raise RuntimeError("Display mesh counts differ from fixture composition. Required %r, observed %r" %
                           (dict(required_meshes), dict(observed)))
    for name, row in shown.items():
        if row.get("visible") is not True or row.get("registered") is not True:
            raise RuntimeError("Display %s (%s) must be visible and registered. Observed visible=%r, registered=%r" %
                               (name, row["mesh"], row.get("visible"), row.get("registered")))


def summarize(data, expected_pid, required_meshes=None):
    samples = data["samples"]
    if len(samples) < 2:
        raise RuntimeError("Insufficient frame samples")
    duration = samples[-1]["seconds"] - samples[0]["seconds"]
    if duration <= 0:
        raise RuntimeError("Frame sample duration must be positive")
    if required_meshes is not None:
        for sample in samples:
            check_displays(sample["poses"], required_meshes)
    changes = {name: 0 for name in samples[0]["poses"]}
    previous = samples[0]["poses"]
    for sample in samples[1:]:
        current = sample["poses"]
        if set(current) != set(previous):
            raise RuntimeError("Display set changed during measurement")
        for name in changes:
            if current[name]["bones"] != previous[name]["bones"]:
                changes[name] += 1
        previous = current
    if any(after["frame"] <= before["frame"] or after["seconds"] <= before["seconds"]
           for before, after in zip(samples, samples[1:])):
        raise RuntimeError("Frame samples must advance monotonically and be deduplicated")
    return {"duration_seconds": duration, "frame_hz": (samples[-1]["frame"] - samples[0]["frame"]) / duration,
            "display_change_hz": {name: count / duration for name, count in changes.items()},
            "samples": len(samples), "foreground_pid": expected_pid,
            "foreground_fraction": sum(row["foreground_pid"] == expected_pid for row in samples) / len(samples),
            "all_preferences_true": all(row["preference"] for row in samples),
            "before": data["before"], "after": data["after"]}


def check_snapshot(snapshot, connected, background):
    if snapshot.get("automation_testing") is not False or snapshot.get("unattended") is not False:
        raise RuntimeError("Ordinary Editor flags missing or incorrect")
    if snapshot.get("throttle_cpu_when_not_foreground") is not True:
        raise RuntimeError("Background CPU preference changed")
    if snapshot.get("foreground") is not (not background) or snapshot.get("has_focus") is not (not background):
        raise RuntimeError("Engine foreground/focus did not match the required real foreground PID")
    if snapshot.get("connected") != connected or snapshot.get("owner_cpu_delegate_count") != int(connected):
        raise RuntimeError("Connection/owned registration did not reach expected state")
    if snapshot.get("should_throttle_cpu_usage") != (background and not connected):
        raise RuntimeError("CPU throttle state did not reach expected state")
    if not snapshot.get("viewports"):
        raise RuntimeError("No level viewport observed")
    if any(not isinstance(view.get("base_realtime"), bool) for view in snapshot["viewports"]):
        raise RuntimeError("Configured viewport realtime state was not observed")
    if background and any(view["realtime"] != connected for view in snapshot["viewports"]):
        raise RuntimeError("Viewport realtime state did not recover")


def validate_fixture(fixture):
    for key in ("primary_mesh", "display_bones", "maya", "oracle_frames", "motion_frames", "thresholds"):
        if not fixture.get(key):
            raise RuntimeError("Fixture requires " + key)
    frames = fixture["motion_frames"]
    if len(frames) != 2 or any(isinstance(value, bool) or not isinstance(value, int) for value in frames) or frames[1] <= frames[0] + 1:
        raise RuntimeError("motion_frames requires an increasing integer range containing distinct producer poses")
    if len(set(fixture["oracle_frames"])) < 2:
        raise RuntimeError("oracle_frames requires at least two distinct stable poses")
    for mesh in required_display_meshes(fixture):
        if not fixture["display_bones"].get(mesh):
            raise RuntimeError("display_bones requires moving oracle bones for " + mesh)
    limits = fixture["thresholds"]
    rationale = limits.get("rationale", "")
    if not isinstance(rationale, str) or not rationale.strip() or "<" in rationale:
        raise RuntimeError("thresholds.rationale must justify this run's limits against AC04 and its same-run reference")
    for key in ("connected_min_hz", "producer_min_hz", "reference_ratio_min", "recovery_ratio_max", "pose_ratio_min", "baseline_max_hz"):
        value = limits.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise RuntimeError("thresholds." + key + " requires an explicit positive finite limit")
    if limits["connected_min_hz"] <= max(3, limits["baseline_max_hz"]):
        raise RuntimeError("connected_min_hz must escape both the 3 Hz engine cap and the baseline limit")
    if not (0 < limits["reference_ratio_min"] <= 1 and 0 < limits["pose_ratio_min"] <= 1 and limits["recovery_ratio_max"] >= 1):
        raise RuntimeError("Reference/pose retention ratios must be in (0, 1], recovery ratio must be at least 1")


def settings_hashes():
    root = Path(os.environ["LOCALAPPDATA"]) / "UnrealEngine" / "5.7" / "Saved" / "Config"
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*Editor*Settings.ini")}


def check_connected_cadence(value, reference, baseline, limits):
    if value["frame_hz"] < max(limits["connected_min_hz"], reference["frame_hz"] * limits["reference_ratio_min"]):
        raise RuntimeError("Background frame cadence failed the declared same-run reference limits")
    if value["frame_hz"] <= baseline["frame_hz"]:
        raise RuntimeError("Connected frame cadence did not escape the measured background baseline")
    if set(value["display_change_hz"]) != set(reference["display_change_hz"]) or not value["display_change_hz"]:
        raise RuntimeError("Connected and reference display sets differ or are empty")
    for measured in (reference, value):
        observable_hz = min(measured["producer_hz"], measured["frame_hz"])
        if measured["producer_hz"] < limits["producer_min_hz"]:
            raise RuntimeError("Insufficient measured producer cadence")
        if any(rate < observable_hz * limits["pose_ratio_min"] for rate in measured["display_change_hz"].values()):
            raise RuntimeError("A display did not follow controlled source poses")
    for name, rate in value["display_change_hz"].items():
        reference_fraction = reference["display_change_hz"][name] / min(reference["producer_hz"], reference["frame_hz"])
        measured_fraction = rate / min(value["producer_hz"], value["frame_hz"])
        if measured_fraction < reference_fraction * limits["pose_ratio_min"]:
            raise RuntimeError("A display lost pose cadence against its same-run reference")


def run(args):
    fixture = read_json(args.fixture)
    validate_fixture(fixture)
    required_meshes = required_display_meshes(fixture)
    out = Path(args.evidence).resolve()
    out.mkdir(parents=True, exist_ok=False)
    config = {"fixture": str(Path(args.fixture).resolve()), "evidence": str(out)}
    write_json(out / "run.json", config)
    env = dict(os.environ, MTOU_BACKGROUND_EDITOR="1", MTOU_BACKGROUND_RUN=str(out / "run.json"))
    command = [args.editor, args.project, "/Engine/Maps/Entry", "-nop4", "-nosplash", "-nocef",
               "-MtoULiveLinkPort=%s" % args.port,
               "-ini:EditorSettings:[/Script/UnrealEd.EditorPerformanceSettings]:bThrottleCPUWhenNotForeground=True",
               "-ExecutePythonScript=" + str(Path(__file__).resolve()), "-abslog=" + str(out / "editor.log")]
    summary = {"product_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "thresholds": fixture["thresholds"], "settings_before": settings_hashes(), "stages": {},
               "oracles": [], "commands": {"editor": command}, "ok": False}
    summary["input_sha256"] = {
        "tool": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "driver": hashlib.sha256(Path(fixture["maya"]["driver"]).read_bytes()).hexdigest(),
        "fixture": hashlib.sha256(Path(args.fixture).read_bytes()).hexdigest()}
    owned_processes = []
    editor = None
    maya = None
    try:
        editor = start_host(command, env, out / "editor-stdout.log", owned_processes)
        editor_box = Mailbox(out / "editor-command.json", out / "editor-state.json", editor)
        summary["pids"] = {"editor": editor.pid, "maya": []}
        wait_for(lambda: host_state(out / "editor-state.json", editor).get("ready"),
                 "ordinary Editor readiness", 300)
        editor_box.send("place")

        def launch_maya(index, connect=True):
            directory = out / ("maya-%s" % index)
            directory.mkdir()
            spec = dict(fixture["maya"], command=str(directory / "command.json"),
                        result=str(directory / "result.json"), evidence=str(directory),
                        start_disconnected=not connect)
            shutil.copytree(spec["app_dir_template"], directory / "appdir")
            write_json(directory / "fixture.json", spec)
            maya_env = dict(os.environ, MAYA_APP_DIR=str(directory / "appdir"),
                            MTOU_LINKAGE_DRIVER_FIXTURE=str(directory / "fixture.json"),
                            MTOU_LIVELINK_PORT=str(args.port))
            startup = 'python(%s)' % json.dumps("import runpy; runpy.run_path(%r)" % Path(spec["driver"]).as_posix())
            maya_command = [spec["exe"], "-command", startup]
            process = start_host(maya_command, maya_env, directory / "stdout.log", owned_processes)
            summary["pids"]["maya"].append(process.pid)
            summary["commands"]["maya-" + str(index)] = maya_command
            box = Mailbox(directory / "command.json", directory / "result.json", process)
            wait_for(lambda: (host_state(box.result, process).get("connected") if connect else
                             host_state(box.result, process).get("phase") == "running"),
                     "Maya readiness", 300)
            return process, box

        maya, maya_box = launch_maya(1)

        def dialogs(box):
            result = read_json(box.result)
            if result.get("errors") or not result.get("ok", False):
                raise RuntimeError("Maya driver failed " + str(result.get("errors")))
            for row in result["dialogs"]:
                allowed = ["BLENDSHAPE_MISMATCH"]
                if row["stage"] in ("destroy_actor", "close_world", "remove_source", "fatal_protocol", "editor_exit"):
                    allowed.append("INVALID_MESSAGE" if row["stage"] == "fatal_protocol" else "STREAM_INTERRUPTED")
                code = (row.get("diagnostic") or {}).get("code")
                if code not in allowed:
                    raise RuntimeError("Unexpected Maya dialog " + str(row))
            summary["maya_preferences"] = result.get("prefs")
            return result

        def stage(name, connected, foreground, produce=False):
            maya_box.send("stage", name=name)
            summary["waiting_for_foreground"] = {"stage": name, "pid": foreground}
            write_json(out / "summary.json", summary)
            print("Waiting passively for %s foreground PID %s" % (name, foreground), flush=True)
            wait_for(lambda: foreground_pid() == foreground, name + " real foreground PID %s" % foreground,
                     args.foreground_timeout)
            summary.pop("waiting_for_foreground")
            window = process_window(editor.pid)
            if win32().IsIconic(window) or not win32().IsWindowVisible(window):
                raise RuntimeError("Measurement Editor must be visible and restored")
            # Wait for the observed state, including one-frame realtime expiry.
            def ready():
                current = editor_box.send("snapshot", name="settle")["snapshot"]
                try:
                    check_snapshot(current, connected, foreground != editor.pid)
                    return current
                except RuntimeError:
                    return False
            wait_for(ready, name + " host state", 30)
            editor_box.send("begin", name=name)
            started = time.monotonic()
            acknowledgements = []
            next_pose = 0
            while time.monotonic() - started < args.seconds:
                if foreground_pid() != foreground:
                    raise RuntimeError(name + " lost required foreground PID")
                if win32().IsIconic(window) or not win32().IsWindowVisible(window):
                    raise RuntimeError(name + " Editor stopped being visible and restored")
                if produce:
                    # Consecutive frames remain distinct within the fixture's
                    # keyed motion. Count actual acknowledgements, not requests.
                    frame = fixture["motion_frames"][0] + next_pose % (fixture["motion_frames"][1] - fixture["motion_frames"][0])
                    response = maya_box.send("time", frame=frame)
                    evaluated = response["time_evaluated"]
                    if evaluated["frame"] != frame:
                        raise RuntimeError("Maya did not evaluate the requested producer frame")
                    acknowledgements.append(dict(evaluated, acknowledged_seconds=time.perf_counter()))
                    next_pose += 1
                else:
                    time.sleep(0.02)
            editor_box.send("end")
            data = read_json(out / (name + ".json"))
            value = summarize(data, foreground, required_meshes if connected else None)
            value["editor_restored"] = not bool(win32().IsIconic(window))
            value["producer_hz"] = ((len(acknowledgements) - 1) / (acknowledgements[-1]["seconds"] - acknowledgements[0]["seconds"])
                                    if len(acknowledgements) > 1 else 0)
            write_json(out / (name + "-producer.json"), acknowledgements)
            summary["stages"][name] = value
            write_json(out / "summary.json", summary)
            if value["foreground_fraction"] != 1 or not value["all_preferences_true"]:
                raise RuntimeError(name + " lost required foreground or preference")
            check_snapshot(value["after"], connected, foreground != editor.pid)
            check_snapshot(value["before"], connected, foreground != editor.pid)
            if ([view["base_realtime"] for view in value["before"]["viewports"]]
                    != [view["base_realtime"] for view in value["after"]["viewports"]]):
                raise RuntimeError(name + " changed configured viewport realtime state")
            dialogs(maya_box)
            print(name, json.dumps({key: value[key] for key in ("frame_hz", "display_change_hz", "producer_hz")}), flush=True)
            return value

        def oracle(name):
            for frame in fixture["oracle_frames"]:
                maya_box.send("time", frame=frame)
                expected = maya_box.send("snapshot")["snapshot"]["joints"]
                observations = []

                def matches():
                    if foreground_pid() != maya.pid:
                        raise RuntimeError("Stable pose oracle requires this run's Maya in the foreground")
                    shown = editor_box.send("pose")["pose"]
                    check_displays(shown, required_meshes)
                    if set(shown) != set(connected["display_change_hz"]):
                        raise RuntimeError("Stable pose oracle display set is incomplete")
                    errors = []
                    for display, row in shown.items():
                        for bone, transform in row["bones"].items():
                            source = expected[bone]["world"]
                            distance = math.sqrt(sum((actual - target) ** 2 for actual, target in
                                                     zip(transform[:3], [source[0], source[2], source[1]])))
                            errors.append(distance)
                    observations.append({"poses": shown, "max_world_error_cm": max(errors) if errors else None})
                    return bool(errors) and max(errors) <= fixture["world_tolerance_cm"]

                try:
                    wait_for(matches, "displayed stable pose at " + str(frame), 15)
                finally:
                    summary["oracles"].append({"stage": name, "frame": frame, "maya": expected, "observations": observations})
                    write_json(out / "summary.json", summary)

        reference = stage("foreground_connected", True, editor.pid, True)
        maya_box.send("disconnect")
        wait_for(lambda: not read_json(maya_box.result).get("session_open", True), "Maya disconnected")
        baseline = stage("background_disconnected", False, maya.pid)
        maya_box.send("connect")
        wait_for(lambda: read_json(maya_box.result).get("connected"), "Maya reconnected")
        connected = stage("background_connected", True, maya.pid, True)
        oracle("background_connected")
        limits = fixture["thresholds"]
        if baseline["frame_hz"] > limits["baseline_max_hz"]:
            raise RuntimeError("Disconnected baseline did not demonstrate background throttling")
        check_connected_cadence(connected, reference, baseline, limits)

        if args.scenario == "editor_exit":
            maya_box.send("stage", name="editor_exit")
            editor_box.send("quit")
        else:
            for action in ("disconnect", "fatal_protocol", "destroy_actor", "close_world", "peer_exit", "remove_source"):
                maya_box.send("stage", name=action)
                if action == "peer_exit":
                    maya_box.send("quit", allow_exit=True)
                    peer_exit_code = maya.wait(timeout=90)
                    summary["peer_exit_code"] = peer_exit_code
                    if peer_exit_code != 0:
                        raise RuntimeError("Maya peer failed normal shutdown")
                    # This test owns the entire Maya peer process. Its real
                    # exit must revoke the editor's background demand.
                    editor_box.send("snapshot", name="peer-exit-immediate")
                    # A replacement owns the next passive foreground check.
                    maya, maya_box = launch_maya(2, connect=False)
                elif action in ("disconnect", "fatal_protocol"):
                    maya_box.send(action)
                else:
                    editor_box.send(action)
                wait_for(lambda: not read_json(maya_box.result).get("session_open", True), "ended Maya session")
                recovered = stage(action + "_recovered", False, maya.pid)
                if recovered["frame_hz"] > max(limits["baseline_max_hz"], baseline["frame_hz"] * limits["recovery_ratio_max"]):
                    raise RuntimeError(action + " failed background cadence recovery")
                if action == "remove_source":
                    break
                if action in ("destroy_actor", "close_world"):
                    editor_box.send("place")
                maya_box.send("connect")
                wait_for(lambda: read_json(maya_box.result).get("connected"), "reconnect after " + action)
                reconnected = stage(action + "_reconnected", True, maya.pid, True)
                check_connected_cadence(reconnected, reference, baseline, limits)
            editor_box.send("quit")
        summary["editor_exit_code"] = editor.wait(timeout=90)
        if summary["editor_exit_code"] != 0:
            raise RuntimeError("Editor failed normal shutdown")
        dialogs(maya_box)
        maya_box.send("quit", allow_exit=True)
        summary["maya_exit_code"] = maya.wait(timeout=90)
        if summary["maya_exit_code"] != 0:
            raise RuntimeError("Maya failed normal shutdown")
        summary["settings_after"] = settings_hashes()
        if summary["settings_after"] != summary["settings_before"]:
            raise RuntimeError("Existing user editor settings changed")
        summary["ok"] = True
    except Exception:
        summary["error"] = traceback.format_exc()
        raise
    finally:
        for process in reversed(owned_processes):
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=90)
            except (OSError, subprocess.TimeoutExpired) as exc:
                summary.setdefault("cleanup_errors", []).append({"pid": process.pid, "error": str(exc)})
                summary["ok"] = False
        summary["settings_after"] = settings_hashes()
        write_json(out / "summary.json", summary)
    if not summary["ok"]:
        raise RuntimeError("Host process cleanup failed, see summary.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", required=True, help="Stock UnrealEditor.exe, with a rendered desktop")
    parser.add_argument("--project", required=True, help="Isolated host loading the checkout and fixture assets")
    parser.add_argument("--fixture", required=True, help="Persistent background-updates fixture JSON")
    parser.add_argument("--evidence", required=True, help="New per-run output directory")
    parser.add_argument("--port", type=int, default=55444, help="Dedicated test listener port")
    parser.add_argument("--seconds", type=float, default=12, help="Measurement window seconds")
    parser.add_argument("--foreground-timeout", type=float, default=90,
                        help="Seconds to wait passively for the required real foreground PID")
    parser.add_argument("--scenario", choices=("exits", "editor_exit"), default="exits")
    args = parser.parse_args()
    if args.port == 54321 or not 1 <= args.port <= 65535 or not math.isfinite(args.seconds) or args.seconds < 8:
        parser.error("Use a dedicated port other than 54321 and at least 8 measurement seconds")
    if not math.isfinite(args.foreground_timeout) or args.foreground_timeout <= 0:
        parser.error("--foreground-timeout must be positive and finite")
    try:
        run(args)
    except Exception as exc:
        print("FAIL " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if os.environ.get("MTOU_BACKGROUND_EDITOR") == "1":
        editor_main()
    else:
        raise SystemExit(main())
