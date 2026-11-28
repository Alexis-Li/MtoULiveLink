# ruff: noqa: E402
"""Opt-in window lifecycle, panel visuals and scene settings in real Maya 2024.

Run with host Python and pass an initialized Maya 2024 preference template.
Each lifecycle scenario opens the packaged panel in a fresh preference copy.
The optional visual scenario records four pages and the complete diagnostics.
The optional scene settings scenario reuses one isolated preference copy in
two successive Maya processes. The template is never changed.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LIFECYCLE_STEPS = ("exit_after_all_closed", "exit_diagnostics_open",
                   "diagnostics_close_cycles", "owner_paths")
SETTINGS_STEPS = ("scene_settings_write", "scene_settings_restore")
STEPS = (*LIFECYCLE_STEPS, "panel_visuals", "scene_settings")
CLOSE_CYCLES = 20
OWNED_ROUNDS = 3
WAIT_SECONDS = 20.0
WM_CLOSE = 0x0010


# ------------------------------------------------------------------ in Maya
def _in_maya():
    import traceback

    import maya.cmds as cmds
    import maya.utils

    step = os.environ["MTOU_WINDOW_STEP"]
    evidence = Path(os.environ["MTOU_WINDOW_EVIDENCE"])
    sys.path.insert(0, str(Path(os.environ["MTOU_WINDOW_RELEASE"]) / "scripts"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import maya_gui_controls as gui
    from maya_gui_panel_scenarios import panel_visuals, scene_settings

    # Its own socket and port, reserved before the packaged component reads PORT.
    listener = None
    if step == "panel_visuals":
        import socket
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        os.environ["MTOU_LIVELINK_PORT"] = str(listener.getsockname()[1])

    callbacks = gui.CallbackCensus.install()
    import MtoULiveLink
    from mtou_livelink.qt_compat import QtCore, maya_main_window

    result = {"step": step, "pid": os.getpid(), "maya_version": cmds.about(version=True),
              "app_dir": Path(os.environ["MAYA_APP_DIR"]).name,
              "api": cmds.about(apiVersion=True), "cut": cmds.about(cutIdentifier=True),
              "entry_file": MtoULiveLink.__file__, "stage": "start", "stages": [],
              "snapshots": [], "errors": [], "quit_requested": False}
    path = evidence / (step + ".json")
    owned_jobs = set()
    panel = None
    controller = None

    def write():
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str),
                        encoding="utf-8")

    def stage(name):
        result["stage"] = name
        result["stages"].append(name)
        write()

    def snapshot(label):
        callback_ids = list(callbacks.live)
        entry = {
            "label": label,
            "windows": {name: len(gui.windows(name, visible_only=False))
                        for name in (gui.MAIN, gui.DIAGNOSTICS)},
            "script_jobs": sum(cmds.scriptJob(exists=job) for job in owned_jobs),
            "callback_ids": callback_ids,
            "scene_callbacks": list(controller._scene_replace_callbacks) if controller else [],
            "exit_callback": controller._maya_exit_callback if controller else None,
            "nodes": sorted(cmds.ls("MtoU_*", long=True) or []),
        }
        result["snapshots"].append(entry)
        write()
        return entry

    def wait(label, predicate):
        """Return to the Qt event loop until a visible or Maya-side change is ready."""
        return label, predicate

    def open_panel():
        nonlocal panel, controller
        stage("open panel")
        panel = MtoULiveLink.run()
        controller = panel.controller
        owned_jobs.update(controller._script_jobs)
        yield wait("panel open", lambda: gui.windows(gui.MAIN) == [panel])
        saved = snapshot("one running panel")
        assert MtoULiveLink.run() is panel and MtoULiveLink.main() is panel
        again = snapshot("repeated run and main")
        for field in ("windows", "script_jobs", "callback_ids"):
            assert again[field] == saved[field], (field, saved, again)
        assert saved["script_jobs"] == 1, saved
        assert len(saved["scene_callbacks"]) == 9, saved
        assert saved["exit_callback"] in saved["callback_ids"], saved
        assert set(saved["scene_callbacks"]).issubset(saved["callback_ids"]), saved

    def close_natively(widget, object_name):
        stage("title-bar close " + object_name)
        post_close(widget)
        yield wait(object_name + " destroyed",
                   lambda: not gui.windows(object_name, visible_only=False))

    def until_idle(label):
        done = []
        cmds.evalDeferred(lambda: done.append(True), lowestPriority=True)
        yield wait(label + " settled", lambda: bool(done))

    def open_diagnostics():
        stage("open complete diagnostics")
        gui.page(panel, gui.DIAGNOSTIC_PAGE)
        gui.click(panel, "diagnosticsButton")
        yield wait("one diagnostic window", lambda: len(gui.windows(gui.DIAGNOSTICS)) == 1)
        details, = gui.windows(gui.DIAGNOSTICS)
        gui.click(panel, "diagnosticsButton")
        assert gui.windows(gui.DIAGNOSTICS, visible_only=False) == [details]
        return details

    def close_panel():
        yield from close_natively(panel, gui.MAIN)
        yield wait("owned diagnostics destroyed",
                   lambda: not gui.windows(gui.DIAGNOSTICS, visible_only=False))
        entry = snapshot("all windows closed")
        assert controller.closed and not any(entry["windows"].values()), entry
        assert not entry["script_jobs"] and not entry["callback_ids"] and not entry["nodes"], entry

    def exit_after_all_closed():
        yield from open_panel()
        yield from open_diagnostics()
        yield from close_panel()

    def exit_diagnostics_open():
        yield from open_panel()
        yield from open_diagnostics()

    def diagnostics_close_cycles():
        yield from open_panel()
        baseline = snapshot("diagnostics cycles baseline")
        for cycle in range(CLOSE_CYCLES):
            details = yield from open_diagnostics()
            yield from close_natively(details, gui.DIAGNOSTICS)
            entry = snapshot("diagnostics cycle " + str(cycle + 1))
            for field in ("windows", "script_jobs", "callback_ids", "nodes"):
                assert entry[field] == baseline[field], (field, entry, baseline)
        yield from close_panel()

    def owner_paths():
        scene = evidence / (step + "-scene.ma")
        cmds.file(rename=str(scene))
        cmds.file(save=True, type="mayaAscii", force=True)
        yield from open_panel()
        for cycle in range(OWNED_ROUNDS):
            yield from open_diagnostics()
            yield from close_panel()
            yield from open_panel()
            details = yield from open_diagnostics()
            gui.page(panel, gui.CONNECTION)
            assert gui.control(panel, "setSecondaryButton").isVisible()
            gui.click(panel, "workflowModel")
            gui.click(panel, "workflowAnimation")
            assert gui.windows(gui.DIAGNOSTICS) == [details]
            for label, replace in (("new scene", lambda: cmds.file(new=True, force=True)),
                                   ("open scene", lambda: cmds.file(str(scene), open=True,
                                                                    force=True))):
                stage(label)
                replace()
                yield from until_idle(label)
                assert gui.windows(gui.MAIN) == [panel]
                assert len(gui.windows(gui.DIAGNOSTICS, visible_only=False)) <= 1
                assert not panel.controller.objects_state()["secondary"]
                yield from open_diagnostics()
            entry = snapshot("scene replacement round " + str(cycle + 1))
            assert entry["script_jobs"] == 1 and len(entry["callback_ids"]) == 10, entry

    def visual_scenario():
        yield from open_panel()
        yield from panel_visuals(panel, listener, evidence, result, stage, wait)
        yield from close_panel()

    def settings_scenario():
        yield from open_panel()
        yield from scene_settings(evidence, step, result, stage, wait, until_idle,
                                  open_panel, close_panel, lambda: panel)
        yield from close_panel()

    scenario = {
        "exit_after_all_closed": exit_after_all_closed,
        "exit_diagnostics_open": exit_diagnostics_open,
        "diagnostics_close_cycles": diagnostics_close_cycles,
        "owner_paths": owner_paths,
        "panel_visuals": visual_scenario,
        **dict.fromkeys(SETTINGS_STEPS, settings_scenario),
    }[step]()

    def quit_maya():
        snapshot("before quit")
        result["quit_requested"] = True
        stage("quit")
        cmds.file(modified=False)  # the disposable scenario never saves on exit
        if os.environ.get("MTOU_WINDOW_QUIT") == "title-bar":
            try:
                post_close(maya_main_window())
                return
            except RuntimeError as exc:
                result["errors"].append(str(exc))
                write()
        cmds.quit(force=True)

    def advance(pending=None, deadline=None):
        try:
            if pending is not None:
                label, predicate = pending
                if not predicate():
                    if time.monotonic() > deadline:
                        raise RuntimeError("timed out waiting for " + label)
                    QtCore.QTimer.singleShot(50, lambda: advance(pending, deadline))
                    return
            pending = next(scenario)
            QtCore.QTimer.singleShot(
                0, lambda: advance(pending, time.monotonic() + WAIT_SECONDS))
        except StopIteration:
            quit_maya()
        except Exception:
            result["errors"].append(traceback.format_exc())
            quit_maya()

    def when_ready():
        cmds.scriptEditorInfo(historyFilename=str(evidence / (step + "-script-editor.txt")),
                              writeHistory=True)
        stage("ready")
        advance()

    maya.utils.executeDeferred(when_ready)


def post_close(widget):
    import ctypes

    if not ctypes.windll.user32.PostMessageW(int(widget.winId()), WM_CLOSE, 0, 0):
        raise RuntimeError("WM_CLOSE was not posted to " + widget.objectName())


# ------------------------------------------------------------- host driver
def check_template(template):
    """The template's Maya 2024 preference files, or why it cannot be used."""
    template = Path(template)
    if not template.is_dir():
        raise SystemExit("--app-dir-template {0} is not a directory".format(template))
    prefs = sorted(template.glob("2024/**/prefs/userPrefs.mel"))
    if not prefs:
        raise SystemExit(
            "--app-dir-template has no 2024/<language>/prefs/userPrefs.mel: start Maya 2024 "
            "once with MAYA_APP_DIR set to it, answer its first-start dialogs and quit.")
    return [path.relative_to(template).as_posix() for path in prefs]


def script_errors(history):
    """Lines of a Script Editor history that report a Python error or the tool.

    A failing callback (close command, button, script job) leaves the window
    closed and the scenario running; only its traceback shows it. The markers
    are ASCII, so Maya's interface language does not matter.
    """
    if not history.exists():
        return ["no Script Editor history"]
    text = history.read_bytes().decode("utf-8", errors="replace")
    return [line.strip() for line in text.splitlines()
            if "Traceback" in line or "mtou_livelink" in line]


def crash_files(*folders):
    found = []
    for folder in folders:
        for path in Path(folder).rglob("*"):
            name = path.name.lower()
            if path.is_file() and ("crash" in name or name.endswith(".dmp")):
                found.append(path.relative_to(folder.parent).as_posix())
    return sorted(found)


def run_step(maya, template, evidence, release, step, timeout, quit_mode):
    app_name = "scene-settings" if step in SETTINGS_STEPS else step
    app_dir = evidence / ("mayaapp-" + app_name)
    temp = evidence / ("temp-" + step)
    if step == SETTINGS_STEPS[-1]:
        if not app_dir.is_dir():
            raise RuntimeError("the first scene settings process left no preference directory")
    else:
        shutil.copytree(template, app_dir)
    temp.mkdir()
    env = dict(os.environ)
    env.update({"MAYA_APP_DIR": str(app_dir), "TEMP": str(temp), "TMP": str(temp),
                "MTOU_WINDOW_STEP": step, "MTOU_WINDOW_EVIDENCE": str(evidence),
                "MTOU_WINDOW_RELEASE": str(release), "MTOU_WINDOW_QUIT": quit_mode})
    env.pop("PYTHONPATH", None)
    command = ('python("import runpy; runpy.run_path(r\'{0}\', '
               'run_name=\'mtou_window_step\')")').format(Path(__file__).resolve().as_posix())
    started = time.time()
    process = subprocess.Popen([maya, "-command", command], env=env, cwd=str(evidence))
    outcome = {"pid": process.pid, "timed_out": False}
    try:
        outcome["exit_code"] = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()  # only the process this check started
        outcome["timed_out"] = True
        outcome["exit_code"] = process.wait()
    outcome["seconds"] = round(time.time() - started, 1)
    outcome["crash_files"] = crash_files(temp, app_dir)
    outcome["script_errors"] = script_errors(evidence / (step + "-script-editor.txt"))
    path = evidence / (step + ".json")
    outcome["maya"] = json.loads(path.read_text(encoding="utf-8")) if path.exists() \
        else {"errors": ["no result"]}
    maya_result = outcome["maya"]
    outcome["passed"] = bool(
        not outcome["timed_out"] and outcome["exit_code"] == 0
        and not outcome["crash_files"] and not outcome["script_errors"]
        and maya_result.get("quit_requested")
        and not maya_result.get("errors"))
    return outcome


def main(argv):
    import argparse

    from maya_gui_install_check import _build_package, _extract

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--maya", required=True)
    parser.add_argument("--app-dir-template", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--step", action="append", choices=STEPS,
                        help="run only these scenarios (repeatable)")
    parser.add_argument("--quit", choices=("command", "title-bar"), default="command",
                        help="quit with cmds.quit, or close Maya's main window")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)

    template = Path(args.app_dir_template).resolve()
    summary = {"template_prefs": check_template(template), "quit": args.quit, "steps": {}}
    evidence = Path(args.evidence).resolve()
    if evidence.exists():
        parser.error("--evidence must be a new folder: " + str(evidence))
    evidence.mkdir(parents=True)
    archive = _build_package(evidence)
    release = _extract(archive, evidence / "release" / "MtoULiveLink")
    summary["archive"] = archive.name
    for scenario in args.step or LIFECYCLE_STEPS:
        steps = SETTINGS_STEPS if scenario == "scene_settings" else (scenario,)
        for step in steps:
            summary["steps"][step] = run_step(
                args.maya, template, evidence, release, step, args.timeout, args.quit)
            if not summary["steps"][step]["passed"]:
                break
        if scenario == "scene_settings" and all(
                summary["steps"].get(step, {}).get("passed") for step in steps):
            write_process, restore_process = (summary["steps"][step]["maya"] for step in steps)
            if (write_process["pid"] == restore_process["pid"]
                    or write_process["app_dir"] != restore_process["app_dir"]):
                restore_process["errors"].append(
                    "scene settings must use two Maya processes with one shared preference copy")
                summary["steps"][steps[-1]]["passed"] = False
    summary["passed"] = all(outcome["passed"] for outcome in summary["steps"].values())
    (evidence / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({step: {key: outcome[key] for key in (
        "passed", "exit_code", "timed_out", "crash_files", "script_errors", "seconds")}
        | {"stage": outcome["maya"].get("stage"), "errors": outcome["maya"].get("errors")}
        for step, outcome in summary["steps"].items()}, ensure_ascii=False, indent=1))
    return 0 if summary["passed"] else 1


if __name__ == "mtou_window_step":
    _in_maya()
elif __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main(sys.argv[1:]))
