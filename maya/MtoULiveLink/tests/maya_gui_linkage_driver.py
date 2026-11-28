# ruff: noqa: E402
"""Opt-in Maya GUI driver for the Unreal GUI acceptance tests.

Runs inside an interactive Maya session started by the Unreal test:

    maya.exe -command "python(\"import runpy; runpy.run_path(r'<this file>')\")"

with ``MTOU_LINKAGE_DRIVER_FIXTURE`` naming the fixture. The driver opens the
supplied rig without saving it, keys disposable test motion on the controls
that drive the listed joints, starts the tool through its public ``run()``
entry, sets the role and connects through the panel's own Qt controls,
and then follows the commands Unreal writes to the command file. Everything
runs on Maya's main thread from a Qt timer, so the panel's own timers,
scriptJobs and deferred callbacks run exactly as they do for an artist.

Fixture keys: ``scene``, ``root``, ``scripts`` (the tool's ``scripts``
directory), ``command``, ``result``, ``evidence``, ``oracle_joints`` (short
names), ``keyed_joints`` (short names whose orient-constraint driver gets test
keys), ``key_range`` ``[start, end]``, ``key_step``, ``key_degrees``,
optional ``keyed_morphs`` (blendShape aliases keyed between the two weights of
``morph_range``, default ``[0.15, 0.85]``), ``morph_namespace`` (report every
weight of the blendShapes in that namespace whose value is at least 0.05, so a
scene's own animation supplies the Morph oracle without test keys) and
``allow_scene_load_warnings`` and ``start_frame`` (Maya's time before the
first connect). Joint names match without namespaces.

For the reconnect workflow, ``legacy_display`` names the existing Display node
and ``legacy_enum`` its clothing attribute. The driver captures its legacy label
before removing the Display naming and enum-label conventions in memory.
``secondary_root`` names the alternate role in a subject exit. The value
``new_joint`` creates a disposable skinned root in the open scene.

Optional ``secondary`` sets the second object before the first connect, from
the same selection the Connection page reads: ``root``, optional ``display``,
``oracle_joints``, ``keyed_joints`` and ``morph_namespace`` with the meanings
above. Snapshots then carry ``objects`` with ``character`` and ``secondary``
rows of ``joints`` and ``morphs``.

Commands (``{"id": n, "action": ...}``): ``enter``, ``connect``, ``disconnect``,
``leave`` (with ``target`` ``realtime``/``end``), ``workflow`` (``target``
``animation``/``model``), ``mode`` (``target`` ``realtime``/``cached``),
``capture`` (``range`` ``[start, end]``), ``time`` (``frame``), ``snapshot``,
``fatal_protocol`` (send a malformed frame over this test session),
``panel_shot`` (with ``name``, reads the visible 诊断详情 text into
``panel_diagnostics`` under the shot name), ``stage`` (with ``name``: every dialog is
recorded with the latest stage, so the test can tell which ones a scenario
expects), ``sample_plugins`` (with ``name``), ``set_secondary``,
``remove_secondary`` (the Connection page's **设为副对象** and **移除副对象**),
``secondary_is_primary`` (tests the Controller's guard against replacing the
second object with the primary root), ``secondary_page`` (``target``
``connection``/``camera``),
``drop_secondary_once`` (the next linkage group leaves out the second object;
this disposable process only), ``resources`` (the census in ``resources``:
live OpenMaya callbacks counted at their registration, the tool's scriptJobs,
Python threads, open sockets, ``MtoU_*`` nodes and MtoU windows),
``close_panel`` (the panel window closes), ``open_panel`` (``run()`` again,
then set the role and connect), ``new_scene``, ``reopen_scene`` (the fixture
scene again, then connect), ``lock_link_camera`` (``target`` ``unlock``
unlocks and deletes it), ``quit``. Each runs the handler of the panel control
it names. Reconnect commands include ``blendshapes`` (``enabled``),
``set_attrs`` (``edits``, each with ``plug`` and ``value``), ``undo``, ``redo``,
and ``set_role`` (``target`` ``primary``/``alternate``).
The result file includes the session identity, worker attempts, panel state
history, captured mesh paths and revision, the latest linkage status, the
object set and the panel's workflow, mode, cache state, status line and
``controls`` (whether 进入联动, 返回实时, 结束预览, 实时预览, 缓存播放 and
捕获并上传 are enabled); ``ack`` names the last finished command.

``prefs`` describes the preferences this Maya started with, read before Maya
can save them again: the ``pluginPrefs.mel`` in this Maya's own preference
folder (``internalVar(userPrefDir=True)``), that file's SHA-256 and the
plug-ins it loads automatically. ``plugin_samples`` keeps, under each
``sample_plugins`` name, the plug-ins loaded when that command ran, its stage
and the seconds since the driver started; a later command never replaces it.
``plugins`` lists the plug-ins loaded at the latest ``stage`` or ``snapshot``.
"""

import json
import math
import os
import sys
import threading
import time
import traceback
from pathlib import Path

import maya.cmds as cmds
import maya.utils

FIXTURE = json.loads(Path(os.environ["MTOU_LINKAGE_DRIVER_FIXTURE"]).read_text(
    encoding="utf-8-sig"))
SCRIPTS = Path(FIXTURE["scripts"])
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
TESTS = str(Path(__file__).resolve().parent)
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

import maya_gui_controls as gui
import maya_prefs_template
from mtou_livelink.qt_compat import QtCore, QtWidgets

RESULT = {"phase": "starting", "ok": True, "ack": -1, "dialogs": [], "errors": [],
          "events": [], "shots": [], "maya_version": cmds.about(version=True),
          "pid": os.getpid(),
          "prefs": maya_prefs_template.describe(cmds.internalVar(userPrefDir=True),
                                                os.environ.get("MAYA_APP_DIR", ""))}
STARTED = time.time()
ATTEMPTS = []
ATTEMPT_LOCK = threading.Lock()


def install_session_census():
    """Count every transport worker, including failed and automatic starts."""
    from mtou_livelink.session import SenderWorker
    run_worker = SenderWorker.run

    def run(worker):
        with ATTEMPT_LOCK:
            ATTEMPTS.append({"seconds": time.perf_counter(),
                             "stage": RESULT.get("stage", "startup")})
        return run_worker(worker)

    SenderWorker.run = run


def attach_panel_census():
    """Record transient reconnecting states even between driver ticks."""
    CONTROLLER.subscribe(record_connection)
    record_connection()


def panel_status():
    return gui.control(PANEL, "connectionStatus").text() if gui.windows(gui.MAIN) else None


def record_connection(snapshot=None):
    snapshot = snapshot if snapshot is not None else CONTROLLER.snapshot()
    session = CONTROLLER._session
    reconnecting = snapshot["connection"] == "reconnecting"
    # Setting a new role or opening a new scene updates the latest operation.
    # Connection acceptance measures why the old session actually stopped.
    stopped = (CONTROLLER._connection_stop_diagnostic
               if session is None and not reconnecting else None)
    diagnostic = stopped or snapshot.get("diagnostic") or {}
    record = {
        "stage": RESULT.get("stage", "startup"),
        "ready": CONTROLLER._session_ready() if session is not None else False,
        "open": session is not None,
        "session_id": CONTROLLER._session_id_of(session),
        "reconnecting": reconnecting,
        "code": diagnostic.get("code"),
        "status": (stopped or {}).get("summary") or snapshot["status"],
    }
    history = RESULT.setdefault("connections", [])
    if not history or any(history[-1][key] != value for key, value in record.items()):
        history.append(dict(record, seconds=time.perf_counter()))
    with ATTEMPT_LOCK:
        RESULT["connection_attempts"] = list(ATTEMPTS)


def capture_record():
    from mtou_livelink.scene import CharacterSceneError
    scene = CONTROLLER._scene
    if scene is None:
        return None
    try:
        captured = scene.snapshot()
    except CharacterSceneError:
        return None
    return {"root": captured.root, "display": scene._display_path,
            "outfit": captured.outfit, "mesh_paths": captured.mesh_paths,
            "revision": captured.revision, "curves": captured.curve_names}


def write_result():
    data = json.dumps(RESULT, ensure_ascii=False, default=str)
    path = Path(FIXTURE["result"])
    for attempt in range(50):
        try:
            path.write_text(data, encoding="utf-8")
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


def fail(message):
    RESULT["ok"] = False
    RESULT["errors"].append(message)
    write_result()


class DialogObserver(QtCore.QObject):
    """Record the visible Qt dialog before returning it to its default action."""

    def eventFilter(self, dialog, event):
        if (event.type() != QtCore.QEvent.Show
                or not isinstance(dialog, QtWidgets.QDialog)
                or dialog.objectName() in (gui.MAIN, gui.DIAGNOSTICS)
                or not (isinstance(dialog, QtWidgets.QMessageBox) or dialog.isModal())):
            return False
        code = dialog.property("diagnosticCode")
        diagnostic = CONTROLLER.snapshot().get("diagnostic") if code and CONTROLLER else None
        if code and (not diagnostic or diagnostic.get("code") != code):
            diagnostic = {"code": code}
        message = dialog.text() if isinstance(dialog, QtWidgets.QMessageBox) else "\n".join(
            label.text() for label in dialog.findChildren(QtWidgets.QLabel) if label.isVisible())
        RESULT["dialogs"].append({
            "title": dialog.windowTitle(), "message": message,
            "diagnostic": diagnostic, "stage": RESULT.get("stage", "startup")})
        write_result()

        def answer():
            if not dialog.isVisible():
                return
            button = dialog.defaultButton() if isinstance(dialog, QtWidgets.QMessageBox) else None
            if button is not None:
                button.click()
            else:
                dialog.reject()

        QtCore.QTimer.singleShot(0, answer)
        return False


def open_scene():
    """Load the fixture and recreate its disposable animation keys on each open."""
    load_start = time.time()
    try:
        cmds.file(FIXTURE["scene"], open=True, force=True, prompt=False,
                  executeScriptNodes=False, ignoreVersion=True)
    except RuntimeError as exc:
        if not FIXTURE.get("allow_scene_load_warnings") or not cmds.objExists(FIXTURE["root"]):
            raise
        RESULT["scene_load_warning"] = str(exc)[:400]
    RESULT.setdefault("load_seconds", round(time.time() - load_start, 1))
    key_test_motion()
    key_test_morphs()


def joint_path(short, root=None):
    joints = cmds.listRelatives(root or FIXTURE["root"], allDescendents=True, type="joint",
                                fullPath=True) or []
    for path in joints:
        if path.split("|")[-1].split(":")[-1] == short:
            return path
    return None


def object_specs():
    """``(object_id, root, oracle joints, morph namespace)`` of every fixture object."""
    specs = [("character", FIXTURE["root"], FIXTURE.get("oracle_joints", []),
              FIXTURE.get("morph_namespace"))]
    secondary = FIXTURE.get("secondary")
    if secondary:
        specs.append(("secondary", secondary["root"], secondary.get("oracle_joints", []),
                      secondary.get("morph_namespace")))
    return specs


def key_test_motion():
    """Key a slow alternating rotation directly on the listed deform joints.

    Production rigs drive deform joints through constraint chains whose
    controls are themselves driven, so the joint's own rotate input is
    detached and keyed instead. Only this disposable, unsaved process changes.

    The keys change joint rotations only. In a rig whose joints are each
    point-constrained to their controls, such as the C01 production rig, no
    joint position moves, so the oracle's world and screen positions are the
    same at every oracle frame and cannot show which frame a pose came from.
    The oracle joints' world rotations and the keyed Morph weights do differ
    between oracle frames, and the oracle compares both. The keys alternate
    every ``key_step`` frames, so the oracle frames' poses must differ: their
    Maya times must not lie a whole number of ``2 * key_step`` apart, nor
    symmetrically about a key. Those conditions are necessary, not sufficient;
    the Unreal test fails when any two oracle frames' poses are too close.
    """
    start, end = FIXTURE.get("key_range", [0, 2500])
    step = float(FIXTURE.get("key_step", 42))
    degrees = float(FIXTURE.get("key_degrees", 25))
    keyed = []
    listed = [(FIXTURE["root"], short) for short in FIXTURE.get("keyed_joints", [])]
    secondary = FIXTURE.get("secondary") or {}
    listed += [(secondary["root"], short) for short in secondary.get("keyed_joints", [])]
    for index, (root, short) in enumerate(listed):
        path = joint_path(short, root)
        if not path:
            RESULT["errors"].append("no joint named " + short)
            continue
        axis = "XYZ"[index % 3]
        for channel in ("rotate", "rotateX", "rotateY", "rotateZ"):
            plug = "{0}.{1}".format(path, channel)
            for source in cmds.listConnections(plug, source=True, destination=False,
                                               plugs=True) or []:
                cmds.disconnectAttr(source, plug)
        plug = "{0}.rotate{1}".format(path, axis)
        cmds.setAttr(plug, lock=False)
        base = cmds.getAttr(plug)
        frame = float(start)
        sign = 1.0
        while frame <= float(end):
            cmds.setKeyframe(plug, time=frame, value=base + sign * degrees)
            sign = -sign
            frame += step
        keyed.append(short + "." + "rotate" + axis)
    RESULT["keyed"] = keyed


def morph_plugs(name):
    """Every blendShape weight plug whose alias is ``name``."""
    plugs = []
    for deformer in cmds.ls(type="blendShape") or []:
        aliases = cmds.aliasAttr(deformer, query=True) or []
        for alias, attribute in zip(aliases[0::2], aliases[1::2]):
            if alias == name:
                plugs.append(deformer + "." + attribute)
    return plugs


def key_test_morphs():
    """Key every listed Morph between two non-zero weights.

    The rig drives its blendShape weights, so each weight's input is detached
    in this disposable process and keyed instead; the weights alternate out of
    phase so no oracle frame sees them all at one value.
    """
    start, end = FIXTURE.get("key_range", [0, 2500])
    step = float(FIXTURE.get("key_step", 42))
    low, high = FIXTURE.get("morph_range", [0.15, 0.85])
    keyed = []
    for index, name in enumerate(FIXTURE.get("keyed_morphs", [])):
        plugs = morph_plugs(name)
        if not plugs:
            RESULT["errors"].append("no blendShape weight named " + name)
            continue
        for plug in plugs:
            for source in cmds.listConnections(plug, source=True, destination=False,
                                               plugs=True) or []:
                cmds.disconnectAttr(source, plug)
            frame = float(start) + index * step / 3.0
            high_next = index % 2 == 0
            while frame <= float(end):
                cmds.setKeyframe(plug, time=frame, value=high if high_next else low)
                high_next = not high_next
                frame += step
        keyed.append(name)
    RESULT["keyed_morphs"] = keyed


def camera_record():
    """The camera Maya's model panels look through, with the values that frame it."""
    panel = None
    for candidate in cmds.getPanel(type="modelPanel") or []:
        if cmds.modelPanel(candidate, query=True, exists=True):
            panel = candidate
            break
    camera = cmds.modelPanel(panel, query=True, camera=True) if panel else None
    if not camera:
        return None
    shapes = cmds.listRelatives(camera, shapes=True, type="camera", fullPath=True)
    shape = shapes[0] if shapes else camera
    matrix = cmds.xform(camera, query=True, worldSpace=True, matrix=True)
    return {
        "camera": camera, "panel": panel, "matrix": matrix,
        "focal_mm": cmds.getAttr(shape + ".focalLength"),
        "hfa_mm": cmds.getAttr(shape + ".horizontalFilmAperture") * 25.4,
        "vfa_mm": cmds.getAttr(shape + ".verticalFilmAperture") * 25.4,
        "hfo_mm": cmds.getAttr(shape + ".horizontalFilmOffset") * 25.4,
        "vfo_mm": cmds.getAttr(shape + ".verticalFilmOffset") * 25.4,
        "film_fit": cmds.getAttr(shape + ".filmFit"),
        "squeeze": cmds.getAttr(shape + ".lensSqueezeRatio"),
        "resolution": [cmds.getAttr("defaultResolution.width"),
                       cmds.getAttr("defaultResolution.height"),
                       cmds.getAttr("defaultResolution.pixelAspect")],
    }


def screen_point(record, world):
    """Normalized resolution-gate coordinates of one Maya world point (cm).

    Maya cameras look down -Z with +Y up. The film-fit rules follow the Maya
    documentation for the resolution gate against the film gate.
    """
    m = record["matrix"]
    origin = (m[12], m[13], m[14])
    axes = [(m[0], m[1], m[2]), (m[4], m[5], m[6]), (m[8], m[9], m[10])]
    rel = [world[i] - origin[i] for i in range(3)]

    def dot(a, b):
        return sum(a[i] * b[i] for i in range(3))

    def unit(v):
        length = math.sqrt(dot(v, v))
        return [c / length for c in v]

    x = dot(rel, unit(axes[0]))
    y = dot(rel, unit(axes[1]))
    depth = -dot(rel, unit(axes[2]))
    if depth <= 1e-6:
        return None
    film_x = record["focal_mm"] * x / depth + record["hfo_mm"]
    film_y = record["focal_mm"] * y / depth + record["vfo_mm"]
    width, height, pixel_aspect = record["resolution"]
    aspect = float(width) * float(pixel_aspect) / float(height)
    hfa, vfa = record["hfa_mm"] * record["squeeze"], record["vfa_mm"]
    film_aspect = hfa / vfa
    fit = record["film_fit"]
    if fit == 1 or (fit == 0 and aspect > film_aspect) or (fit == 3 and aspect <= film_aspect):
        gate_w, gate_h = hfa, hfa / aspect
    else:
        gate_w, gate_h = vfa * aspect, vfa
    return [film_x / (gate_w / 2.0), film_y / (gate_h / 2.0)]


def world_axes(matrix):
    """The world X, Y and Z axes of a row-major world matrix as unit vectors, flattened."""
    axes = []
    for row in range(3):
        axis = matrix[row * 4:row * 4 + 3]
        length = math.sqrt(sum(c * c for c in axis))
        axes.extend(c / length for c in axis)
    return axes


def snapshot():
    record = camera_record()
    # The joints and weights are evaluated at the current time itself, so the
    # oracle does not depend on whether Maya re-evaluated the scene after the
    # time change: xform and a plain getAttr return the last evaluated values.
    now = cmds.currentTime(query=True)
    objects = {}
    for object_id, root, oracle_joints, namespace in object_specs():
        joints = {}
        for short in oracle_joints:
            path = joint_path(short, root)
            if not path:
                continue
            matrix = cmds.getAttr(path + ".worldMatrix[0]", time=now)
            world = list(matrix[12:15])
            joints[short] = {"world": world,
                             "screen": screen_point(record, world) if record else None,
                             "axes": world_axes(matrix)}
        morphs = {}
        if object_id == "character":
            for name in RESULT.get("keyed_morphs", []):
                plugs = morph_plugs(name)
                if plugs:
                    morphs[name] = cmds.getAttr(plugs[0], time=now)
        if namespace:
            morphs.update(animated_morphs(namespace, now))
        objects[object_id] = {"joints": joints, "morphs": morphs}
    status = CONTROLLER.linkage_status()
    return {"time": now, "camera": record, "joints": objects["character"]["joints"],
            "morphs": objects["character"]["morphs"], "objects": objects, "status": status}


def animated_morphs(namespace, now):
    """Every weight of the namespace's blendShapes at ``now`` that is at least 0.05."""
    weights = {}
    for deformer in cmds.ls(namespace + ":*", type="blendShape") or []:
        aliases = cmds.aliasAttr(deformer, query=True) or []
        for alias in aliases[0::2]:
            value = cmds.getAttr(deformer + "." + alias, time=now)
            if abs(value) >= 0.05:
                weights[alias] = value
    return weights


def grab_windows(name):
    """Save the visible panel and diagnostics window as PNGs."""
    saved = []
    for name_id in (gui.MAIN, gui.DIAGNOSTICS):
        for widget in gui.windows(name_id):
            path = Path(FIXTURE["evidence"]) / f"{name}-{name_id}.png"
            saved.append(gui.save_image(widget, path))
    RESULT["shots"].extend(saved)
    return saved


PANEL = None
CONTROLLER = None
CALLBACKS = None
DIALOG_OBSERVER = None
LAST_ID = [-1]
TIMER = [None]


def record_plugins():
    RESULT["plugins"] = {"stage": RESULT.get("stage", "startup"),
                         "loaded": sorted(cmds.pluginInfo(query=True, listPlugins=True) or [])}


# Every scriptJob ID a panel of this process registered, including closed panels.
TOOL_SCRIPT_JOBS = set()


def census():
    """What the tool holds in this Maya right now."""
    import gc
    import socket
    import threading
    families = {}
    for family in CALLBACKS.live.values():
        families[family] = families.get(family, 0) + 1
    # Only the jobs a panel registered: Maya's own come and go on their own.
    if CONTROLLER is not None:
        TOOL_SCRIPT_JOBS.update(CONTROLLER._script_jobs)
    jobs = [job for job in TOOL_SCRIPT_JOBS if cmds.scriptJob(exists=job)]
    sockets = 0
    for item in gc.get_objects():
        try:
            if isinstance(item, socket.socket) and item.fileno() != -1:
                sockets += 1
        except Exception:
            continue
    windows = [name for name in (gui.MAIN, gui.DIAGNOSTICS) if gui.windows(name)]
    return {"callbacks": len(CALLBACKS.live), "callback_families": families,
            "script_jobs": len(jobs),
            "threads": sorted(thread.name for thread in threading.enumerate()
                              if thread is not threading.main_thread()),
            "sockets": sockets,
            "nodes": sorted(cmds.ls("MtoU_*") or []),
            "windows": windows,
            "session_open": CONTROLLER is not None and CONTROLLER._session is not None}


def scene_state():
    """What linkage captures and must put back: time, range, gate, cameras, undo."""
    from mtou_livelink.camera import RESOLUTION_PLUGS, model_panels, panel_camera
    return {
        "time": cmds.currentTime(query=True),
        "range": [cmds.playbackOptions(query=True, **{flag: True})
                  for flag in ("minTime", "maxTime", "animationStartTime",
                               "animationEndTime")],
        "gate": dict((plug, cmds.getAttr(plug)) for plug in RESOLUTION_PLUGS),
        "panels": dict((panel, panel_camera(panel)) for panel in model_panels()),
        "undo": bool(cmds.undoInfo(query=True, state=True)),
        "link_camera": bool(cmds.objExists("MtoU_LinkCamera")),
    }


def launch_panel():
    global CONTROLLER, PANEL
    import MtoULiveLink

    if CONTROLLER is not None:
        TOOL_SCRIPT_JOBS.update(CONTROLLER._script_jobs)
    PANEL = MtoULiveLink.run()
    CONTROLLER = PANEL.controller
    TOOL_SCRIPT_JOBS.update(CONTROLLER._script_jobs)
    attach_panel_census()


def open_panel():
    """The public entry again, then the role and the connection."""
    launch_panel()
    connect_role()


def reopen_scene():
    """Open the fixture scene again, as an artist does, and connect."""
    open_scene()
    # No flush: whatever Maya still runs from the idle queue about the open
    # must leave the role set below alone, as it must for a quick artist.
    if "start_frame" in FIXTURE:
        cmds.currentTime(float(FIXTURE["start_frame"]), update=True)
    connect_role()


def prepare_root_only():
    """Keep the rig's outfit connections while removing legacy discovery conventions."""
    display = FIXTURE["legacy_display"]
    attribute = FIXTURE["legacy_enum"]
    if not cmds.objExists(display + "." + attribute):
        raise ValueError("missing legacy Display enum " + display + "." + attribute)
    connect_role(connect=False)
    RESULT["legacy_capture"] = capture_record()
    renamed = cmds.rename(display, "MtoU_ClothingContext")
    labels = cmds.attributeQuery(attribute, node=renamed, listEnum=True)[0].split(":")
    cmds.addAttr(renamed + "." + attribute, edit=True,
                 enumName=":".join("Option" + str(index) for index in range(len(labels))))


def edit_attrs(edits):
    """One Maya undoable edit, with fixture prerequisites checked before mutation."""
    for edit in edits:
        if not cmds.objExists(edit["plug"]) or not cmds.getAttr(edit["plug"], settable=True):
            raise ValueError("cannot edit fixture plug " + edit["plug"])
    cmds.undoInfo(openChunk=True, chunkName="MtoU GUI acceptance")
    try:
        for edit in edits:
            cmds.setAttr(edit["plug"], edit["value"])
    finally:
        cmds.undoInfo(closeChunk=True)


def alternate_root():
    root = FIXTURE["secondary_root"]
    if root != "new_joint":
        return root
    root = "|MtoU_AlternateRoot"
    if not cmds.objExists(root):
        cmds.select(clear=True)
        cmds.joint(name=root[1:])
        mesh = cmds.polyCube(name="MtoU_AlternateMesh")[0]
        cmds.skinCluster(root, mesh, toSelectedBones=True)
    return root


def connect_role(connect=True):
    cmds.select(FIXTURE["root"], replace=True)
    gui.page(PANEL, gui.CONNECTION)
    gui.click(PANEL, "setRoleButton")
    if FIXTURE.get("secondary") and not CONTROLLER.objects_state().get("secondary"):
        select_secondary()
        gui.click(PANEL, "setSecondaryButton")
    if connect:
        gui.click(PANEL, "connectButton")


REALTIME_PLAYBACK_OPTIONS = {}


def probe_realtime_performance(command):
    operation = command["operation"]
    session = CONTROLLER._session
    metrics = session.realtime_metrics() if session is not None else None
    if metrics is None:
        raise RuntimeError("Real-time performance requires a working Maya streaming session")
    if operation == "start":
        first, last = command["range"]
        if not math.isfinite(first) or not math.isfinite(last) or first >= last:
            raise ValueError("Real-time performance requires an increasing Maya playback range")
        if cmds.play(query=True, state=True):
            raise RuntimeError("Stop Maya playback before starting the real-time performance run")
        options = {"minTime": first, "maxTime": last, "loop": "continuous", "by": 1.0,
                   "playbackSpeed": 1.0}
        REALTIME_PLAYBACK_OPTIONS.update(
            (name, cmds.playbackOptions(query=True, **{name: True})) for name in options)
        cmds.playbackOptions(**options)
        cmds.currentTime(first, update=True)
        cmds.play(forward=True)
    elif operation == "stop":
        cmds.play(state=False)
        cmds.playbackOptions(**REALTIME_PLAYBACK_OPTIONS)
        REALTIME_PLAYBACK_OPTIONS.clear()
    elif operation != "snapshot":
        raise ValueError("Unknown real-time performance operation: " + operation)
    RESULT["realtime_performance"] = {
        "seconds": time.perf_counter(),
        "playing": bool(cmds.play(query=True, state=True)),
        "time": cmds.currentTime(query=True),
        "maya_playback_fps": cmds.playbackOptions(query=True, framesPerSecond=True),
        "metrics": metrics,
    }


def run_command(command):
    action = command.get("action")
    if action == "enter":
        gui.page(PANEL, gui.CAMERA)
        gui.click(PANEL, "enterLinkageButton")
    elif action == "connect":
        if CONTROLLER._session is None and CONTROLLER.snapshot()["connection"] != "reconnecting":
            gui.click(PANEL, "connectButton")
    elif action == "leave":
        gui.page(PANEL, gui.CAMERA)
        target = command.get("target", "realtime")
        gui.click(PANEL, "endLinkageButton" if target == "end" else "leaveLinkageButton")
    elif action == "stage":
        # Names the scenario the following dialogs belong to.
        RESULT["stage"] = command.get("name") or "startup"
        record_plugins()
    elif action == "realtime_performance":
        probe_realtime_performance(command)
    elif action == "snapshot":
        RESULT["snapshot"] = snapshot()
        record_plugins()
    elif action == "sample_plugins":
        maya_prefs_template.record_plugin_sample(
            RESULT, command.get("name") or "sample", RESULT.get("stage", "startup"),
            cmds.pluginInfo(query=True, listPlugins=True) or [], time.time() - STARTED)
    elif action == "panel_shot":
        name = command.get("name", "panel")
        was_open = bool(gui.windows(gui.DIAGNOSTICS))
        diagnostic_window = PANEL.show_diagnostics()
        details = gui.control(diagnostic_window, "diagnosticDetails").toPlainText()
        RESULT.setdefault("panel_diagnostics", {})[name] = details
        RESULT.setdefault("panel_latest_diagnostics", {})[name] = gui.control(
            PANEL, "diagnosticLatest").toPlainText()
        RESULT.setdefault("panel_capture_state", {})[name] = {
            "connection": gui.control(PANEL, "connectionStatus").text(),
            "authority": gui.control(PANEL, "timeAuthority").text(),
            "beam_animation": PANEL.beam.animation_active,
        }
        grab_windows(name)
        if not was_open:
            diagnostic_window.close()
    elif action == "time":
        preserve = command.get("preserve_undo") and cmds.undoInfo(query=True, state=True)
        if preserve:
            cmds.undoInfo(stateWithoutFlush=False)
        try:
            cmds.currentTime(float(command["frame"]), update=True)
        finally:
            if preserve:
                cmds.undoInfo(stateWithoutFlush=True)
        RESULT["time_evaluated"] = {"frame": cmds.currentTime(query=True),
                                    "seconds": time.perf_counter()}
    elif action == "disconnect":
        if CONTROLLER._session is not None or CONTROLLER.snapshot()["connection"] == "reconnecting":
            gui.click(PANEL, "connectButton")
    elif action == "fatal_protocol":
        # A real malformed packet on the existing session exercises the fatal
        # protocol end path. The product's serializer and sender stay in use.
        CONTROLLER._session._worker.submit_control(
            {"type": "frame", "transforms": None, "curves": []})
    elif action == "workflow":
        gui.page(PANEL, gui.CONNECTION)
        target = command["target"]
        gui.click(PANEL, "workflowModel" if target == "model" else "workflowAnimation")
    elif action == "blendshapes":
        gui.page(PANEL, gui.CONNECTION)
        gui.set_checked(PANEL, "blendshapesCheck", command["enabled"])
    elif action == "set_attrs":
        edit_attrs(command["edits"])
    elif action in ("undo", "redo"):
        name = cmds.undoInfo(query=True, **{action + "Name": True})
        if name != "MtoU GUI acceptance":
            raise RuntimeError("next Maya %s is not the test edit: %s" % (action, name))
        {"undo": cmds.undo, "redo": cmds.redo}[action]()
    elif action == "set_role":
        root = alternate_root() if command["target"] == "alternate" else FIXTURE["root"]
        cmds.select(root, replace=True)
        gui.page(PANEL, gui.CONNECTION)
        gui.click(PANEL, "setRoleButton")
    elif action == "mode":
        gui.page(PANEL, gui.PREVIEW)
        target = command["target"]
        gui.click(PANEL, "cachedMode" if target == "cached" else "realtimeMode")
    elif action == "capture":
        start, end = command["range"]
        gui.set_capture_range(PANEL, start, end)
        gui.click(PANEL, "captureButton")
    elif action == "set_secondary":
        select_secondary()
        gui.page(PANEL, gui.CONNECTION)
        gui.click(PANEL, "setSecondaryButton")
    elif action == "secondary_is_primary":
        # With a second object already set, the page only offers its removal.
        # Exercise the replacement guard directly so the old set must survive.
        cmds.select(FIXTURE["root"], replace=True)
        CONTROLLER.set_secondary_object()
    elif action == "remove_secondary":
        gui.page(PANEL, gui.CONNECTION)
        gui.click(PANEL, "removeSecondaryButton")
    elif action == "secondary_page":
        target = gui.CAMERA if command["target"] == "camera" else gui.CONNECTION
        gui.page(PANEL, target)
        root_label = gui.control(PANEL, "secondaryRoot")
        root = CONTROLLER.objects_state()["secondary"]
        label_matches = root in (root_label.text(), root_label.toolTip())
        RESULT["secondary_page"] = {
            "connection_page": target == gui.CONNECTION,
            "secondary_visible": bool(root and root_label.isVisible() and label_matches
                                      and gui.control(PANEL, "removeSecondaryButton").isVisible()),
        }
    elif action == "drop_secondary_once":
        drop_secondary_once()
    elif action == "resources":
        RESULT["resources"] = census()
    elif action == "scene_state":
        RESULT["scene_state"] = scene_state()
    elif action == "close_panel":
        # The panel window's own close, as its title bar does.
        PANEL.close()
    elif action == "open_panel":
        open_panel()
    elif action == "new_scene":
        cmds.file(new=True, force=True)
    elif action == "reopen_scene":
        reopen_scene()
    elif action == "lock_link_camera":
        # A restore that cannot delete the tool's camera: its residual must be
        # reported, and the person removes the node.
        locking = command.get("target") != "unlock"
        cmds.lockNode("MtoU_LinkCamera", lock=locking)
        if not locking:
            cmds.delete("MtoU_LinkCamera")
    elif action == "quit":
        quit_maya()
        return
    else:
        RESULT["errors"].append("unknown command " + str(action))


def select_secondary():
    secondary = FIXTURE["secondary"]
    selection = [secondary["root"]]
    if secondary.get("display"):
        selection.append(secondary["display"])
    cmds.select(selection, replace=True)


def drop_secondary_once():
    """The next linkage group leaves out the second object's row.

    Patches the group builder the linkage session imported, in this disposable
    process only, so Unreal receives one incomplete group through the real
    connection; every later group is complete again.
    """
    from mtou_livelink import linkage
    original = linkage.make_group_message

    def incomplete(*args, **kwargs):
        linkage.make_group_message = original
        message = original(*args, **kwargs)
        message["objects"] = [row for row in message["objects"]
                              if row.get("object_id") != "secondary"]
        RESULT["dropped_group"] = {"eval_id": message.get("eval_id"),
                                   "objects": [row.get("object_id") for row in message["objects"]]}
        return message

    linkage.make_group_message = incomplete


PANEL_CONTROLS = (("enter", "enterLinkageButton"), ("leave_realtime", "leaveLinkageButton"),
                  ("leave_end", "endLinkageButton"), ("realtime", "realtimeMode"),
                  ("cached", "cachedMode"), ("capture", "captureButton"))


def control_states():
    """Enabled controls, including those on other tabs; None with the panel closed."""
    return {name: gui.control(PANEL, control).isEnabled() if gui.windows(gui.MAIN) else None
            for name, control in PANEL_CONTROLS}


def tick():
    try:
        status = CONTROLLER.linkage_status()
        RESULT["status"] = status
        RESULT["objects"] = CONTROLLER.objects_state()
        # Unreal's last applied evidence, one component row per object.
        applied = getattr(CONTROLLER._linkage, "_applied", None)
        RESULT["applied"] = dict(applied) if applied else None
        RESULT["connected"] = CONTROLLER._session is not None and CONTROLLER._session_ready()
        # 连接 does nothing while an ending session is still being torn down.
        RESULT["session_open"] = CONTROLLER._session is not None
        record_connection()
        if FIXTURE.get("legacy_display"):
            RESULT["capture"] = capture_record()
        RESULT["maya_time"] = cmds.currentTime(query=True)
        RESULT["link_camera_exists"] = bool(cmds.objExists("MtoU_LinkCamera"))
        RESULT["panel_camera"] = (camera_record() or {}).get("camera")
        view = CONTROLLER._cached_view
        ui_state = CONTROLLER.snapshot()
        RESULT["panel"] = {
            "workflow": CONTROLLER.settings.workflow, "mode": ui_state["mode"],
            "cached_state": getattr(view, "state", None) if view else None,
            "status": panel_status(),
            "controls": control_states()}
        try:
            command = json.loads(Path(FIXTURE["command"]).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            command = {}
        if command.get("id", -1) > LAST_ID[0]:
            LAST_ID[0] = command["id"]
            run_command(command)
            RESULT["ack"] = command["id"]
        write_result()
    except Exception:
        RESULT["errors"].append(traceback.format_exc()[-1500:])
        write_result()


def quit_maya():
    TIMER[0].stop()
    RESULT["phase"] = "quitting"
    try:
        if gui.windows(gui.MAIN):
            PANEL.close()
    except Exception:
        RESULT["errors"].append("close: " + traceback.format_exc()[-800:])
    # The supplied rig is never saved; the keyed copy only lived in memory.
    cmds.file(modified=False)
    RESULT["phase"] = "quit"
    write_result()
    cmds.quit(force=True)


def start():
    global CALLBACKS, DIALOG_OBSERVER
    try:
        DIALOG_OBSERVER = DialogObserver()
        QtWidgets.QApplication.instance().installEventFilter(DIALOG_OBSERVER)
        RESULT["phase"] = "loading"
        write_result()
        open_scene()
        CALLBACKS = gui.CallbackCensus.install()
        install_session_census()
        launch_panel()
        if FIXTURE.get("legacy_display"):
            prepare_root_only()
        if FIXTURE.get("initial_edits"):
            edit_attrs(FIXTURE["initial_edits"])
        RESULT["phase"] = "panel"
        write_result()
        if "start_frame" in FIXTURE:
            # Where the artist starts: linkage maps every target from here.
            cmds.currentTime(float(FIXTURE["start_frame"]), update=True)
        connect_role(connect=not FIXTURE.get("start_disconnected", False))
        RESULT["phase"] = "running"
        write_result()
        TIMER[0] = QtCore.QTimer()
        TIMER[0].timeout.connect(tick)
        TIMER[0].start(50)
    except Exception:
        fail(traceback.format_exc()[-2000:])


maya.utils.executeDeferred(start)
