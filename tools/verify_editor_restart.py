"""Verify that an ordinary Editor restart leaves a running Maya disconnected."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

TEST = "MtoUOptIn.Editor.Workflow.GuiRegression"
RESTART_WINDOW = 32.0
REPO = Path(__file__).resolve().parents[1]


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def wait_for(predicate, timeout, description, process=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"Editor exited ({process.returncode}) before {description}")
        time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {description}")


def start_editor(args, evidence, fixture, phase):
    command = [
        str(args.editor), str(args.project), "/Engine/Maps/Entry",
        "-unattended", "-nopause", "-nosplash", "-NullRHI", "-nocef",
        f"-MtoULiveLinkPort={args.port}", f"-MtoUGuiWorkflowFixture={fixture}",
        f"-MtoUGuiReconnectEditor{phase}",
        f"-AbsLog={evidence / (phase.lower() + '.log')}",
        f"-ReportExportPath={evidence / phase.lower()}",
        f"-ExecCmds=Automation RunTests {TEST}; Quit",
        "-TestExit=Automation Test Queue Empty",
    ]
    return subprocess.Popen(command)


def assert_passed(process, report_dir):
    if process.wait(timeout=90):
        raise RuntimeError(f"Editor returned {process.returncode}; see {report_dir}")
    report = read_json(report_dir / "index.json")
    rows = [test for test in report.get("tests", [])
            if test.get("fullTestPath") == TEST]
    if len(rows) != 1 or rows[0].get("state") != "Success":
        raise RuntimeError(f"The Editor automation report did not pass; see {report_dir}")


def quit_maya(evidence):
    result_path = evidence / "maya-result.json"
    result = read_json(result_path)
    if result.get("phase") in (None, "not-started", "quit"):
        return
    command = evidence / "maya-command.json"
    temp = evidence / "maya-command-exit.tmp"
    temp.write_text(json.dumps({"id": result["ack"] + 1, "action": "quit"}), encoding="utf-8")

    def send():
        try:
            temp.replace(command)
            return True
        except PermissionError:
            return False

    wait_for(send, 5, "Maya's quit command to be written")
    wait_for(lambda: read_json(result_path).get("phase") == "quit", 45,
             "Maya to acknowledge resource cleanup and quit")


def verify(args):
    original = args.fixture.resolve(strict=True)
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    fixture_path = evidence / "workflow-reconnect.json"
    fixture = read_json(original)
    if not isinstance(fixture.get("reconnect"), dict):
        raise ValueError("The fixture must contain the reconnect workflow settings")
    fixture["evidence"] = evidence.as_posix()
    fixture["maya"]["driver"] = (REPO / "maya/MtoULiveLink/tests/maya_gui_linkage_driver.py").as_posix()
    fixture["maya"]["scripts"] = (REPO / "maya/MtoULiveLink/scripts").as_posix()
    fixture_path.write_text(json.dumps(fixture, indent=2), encoding="utf-8")

    processes = []
    try:
        first = start_editor(args, evidence, fixture_path, "Exit")
        processes.append(first)
        old = wait_for(lambda: read_json(evidence / "editor-exit-ready.json"), 700,
                       "the first Editor and Maya to be connected", first)
        assert_passed(first, evidence / "exit")

        result_path = evidence / "maya-result.json"
        prior = wait_for(lambda: read_json(result_path), 5, "the surviving Maya result")
        second = start_editor(args, evidence, fixture_path, "Probe")
        processes.append(second)
        new = wait_for(lambda: read_json(evidence / "editor-probe-ready.json"), 120,
                       "the replacement Editor's target to load", second)
        if old["editor_pid"] == new["editor_pid"]:
            raise RuntimeError("The replacement must run in a different Editor process")

        def observe(require_stopped=False):
            if second.poll() is not None:
                raise RuntimeError("The replacement Editor exited during observation")
            result = read_json(result_path)
            if not result:
                return None
            if result.get("pid") != prior.get("pid") or result.get("phase") != "running":
                raise RuntimeError("The original Maya process did not survive the Editor restart")
            if len(result.get("connection_attempts", [])) != old["attempts"]:
                raise RuntimeError("Maya attempted another connection without a Connect command")
            events = result.get("connections", [])
            if not events:
                return {}
            state = events[-1]
            stopped = (not any(state.get(flag) for flag in ("open", "ready", "reconnecting"))
                       and state.get("code") in {"STREAM_INTERRUPTED", "SUBJECT_CHANGED"}
                       and state.get("status"))
            if not stopped:
                if require_stopped:
                    raise RuntimeError("Maya left its terminal state after the replacement Editor opened")
                return None
            unexpected = [row for row in result.get("dialogs", [])
                          if (row.get("diagnostic") or {}).get("code")
                          not in {"BLENDSHAPE_MISMATCH", state["code"]}]
            if unexpected or result.get("errors"):
                raise RuntimeError("Maya recorded an unexpected dialog or driver error")
            return {"state": state, "pid": result["pid"], "prefs": result["prefs"]}

        stopped = wait_for(observe, 20, "Maya to report why the Editor session stopped")
        deadline = time.monotonic() + RESTART_WINDOW
        while time.monotonic() < deadline:
            observe(require_stopped=True)
            time.sleep(0.1)
        wait_for(lambda: observe(require_stopped=True), 2, "a final stopped Maya result")
        record = {
            "before": old,
            "new_editor": new,
            "maya": stopped,
            "observation_seconds": RESTART_WINDOW,
            "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        }
        marker = evidence / "editor-probe-done.json"
        marker.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        assert_passed(second, evidence / "probe")
        return marker
    finally:
        try:
            quit_maya(evidence)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", type=Path, required=True, help="UnrealEditor-Cmd executable")
    parser.add_argument("--project", type=Path, required=True, help="disposable Unreal project")
    parser.add_argument("--fixture", type=Path, required=True, help="workflow-reconnect.json")
    parser.add_argument("--evidence", type=Path, required=True, help="new output directory")
    parser.add_argument("--port", type=int, required=True, help="dedicated test port, excluding 54321")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.port == 54321:
        parser.error("--port must be a valid dedicated test port other than 54321")
    if not args.editor.is_file() or not args.project.is_file():
        parser.error("--editor and --project must name existing files")
    try:
        print(verify(args))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
