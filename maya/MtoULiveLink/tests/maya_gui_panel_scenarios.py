"""Real Maya acceptance scenes for panel visuals, diagnostics and saved settings."""

import hashlib
import socket
import threading

import maya.cmds as cmds
import maya_gui_controls as gui


def _character(group_name, root_name, outfit, display=False, morph=None):
    group = cmds.group(empty=True, name=group_name)
    root = cmds.createNode("joint", name=root_name, parent=group)
    mesh = cmds.polyCube(name=outfit, constructionHistory=False)[0]
    mesh = cmds.parent(mesh, group)[0]
    if morph:
        target = cmds.duplicate(mesh, name=morph)[0]
        cmds.move(0.5, 0, 0, f"{target}.vtx[0]", relative=True)
        cmds.blendShape(target, mesh, name="FixtureBlendShape")
        cmds.delete(target)
    cmds.skinCluster(root, mesh, toSelectedBones=True)
    node = None
    if display:
        node = cmds.circle(name="FixtureDisplay", constructionHistory=False)[0]
        node = cmds.parent(node, group)[0]
        cmds.addAttr(node, longName="wardrobe", attributeType="enum",
                     enumName="ClothesAcceptance:ClothesAlternate")
        node = cmds.ls(node, long=True)[0]
    return cmds.ls(root, long=True)[0], node


def _set_role(panel, root):
    gui.page(panel, gui.CONNECTION)
    cmds.select(root, replace=True)
    gui.click(panel, "setRoleButton")
    assert panel.controller.snapshot()["root"] == root


def _set_secondary(panel, root):
    gui.page(panel, gui.CONNECTION)
    cmds.select(root, replace=True)
    gui.click(panel, "setSecondaryButton")
    assert panel.controller.objects_state()["secondary"] == root


def _elision(panel, control_name, expected):
    label = gui.control(panel, control_name)
    text = label.text()
    assert label.isVisible() and label.toolTip() == expected, (control_name, label.toolTip())
    assert text and text != expected and "…" in text, (control_name, text)
    bounds = label.contentsRect().width()
    assert label.fontMetrics().horizontalAdvance(text) <= bounds, (control_name, text, bounds)
    return {"visible": text, "tooltip": label.toolTip(), "width": bounds}


def panel_visuals(panel, listener, evidence, result, stage, wait):
    from mock_linkage_server import MockUnrealPeer, encode
    from mtou_livelink import qt_compat
    from mtou_livelink.qt_compat import QtWidgets

    long_detail = "故障详情首行\n" + "\n".join(
        f"第 {index:02d} 行｜" + "角色资源缺少可用的骨骼网格，" * 9 for index in range(1, 31)
    ) + "\n故障详情末行"

    class VisualPeer(MockUnrealPeer):
        def _send(self, sock, message):
            if message["type"] == "ready":
                message.update(missing_in_unreal=["FixtureSmile"], accepted_morph_count=0,
                               target_morph_count=0)
            super()._send(sock, message)

    peer = VisualPeer(listener.getsockname()[1], targets=20)
    clients = []

    def serve():
        try:
            with listener:
                client, _ = listener.accept()
            with client:
                clients.append(client)
                peer.serve(client)
        except (OSError, ValueError) as exc:
            peer.errors.append(str(exc))

    threading.Thread(target=serve, name="MtoU visual acceptance peer", daemon=True).start()
    root, _ = _character("LongCharacter" * 9, "LongDeformationRoot" * 6,
                         "LongFixtureGarment" * 9, morph="FixtureSmile")
    _set_role(panel, root)
    controller = panel.controller
    outfit = controller.snapshot()["outfit"]
    assert outfit and "LongFixtureGarment" in outfit

    def shot(name, widget=panel):
        output = evidence / (name + ".png")
        result.setdefault("shots", []).append(gui.save_image(widget, output))

    stage("connect with a difference and silence popups")
    gui.page(panel, gui.DIAGNOSTIC_PAGE)
    gui.set_checked(panel, "popupCheck", False)
    gui.click(panel, "connectButton")
    yield wait("real connection with a difference", lambda:
               controller.snapshot()["connection"] == "connected"
               and controller.snapshot()["connection_difference"])
    assert not any(dialog.isVisible() for dialog in QtWidgets.QApplication.topLevelWidgets()
                   if isinstance(dialog, QtWidgets.QMessageBox))
    gui.page(panel, gui.CONNECTION)
    yield wait("connection page layout", lambda: gui.control(panel, "rootPath").isVisible())
    result["elision"] = {
        "root": _elision(panel, "rootPath", root),
        "outfit": _elision(panel, "outfitName", outfit),
    }
    shot("01-connection")

    stage("system animation disabled and reenabled")
    animations_enabled = qt_compat.animations_enabled
    try:
        qt_compat.animations_enabled = lambda: True
        yield wait("animated realtime beam", lambda: panel.beam.animation_active)
        qt_compat.animations_enabled = lambda: False
        yield wait("static realtime beam", lambda: not panel.beam.animation_active)
        result["realtime_beam_static"] = True
    finally:
        qt_compat.animations_enabled = animations_enabled

    stage("preview page")
    gui.page(panel, gui.PREVIEW)
    yield wait("preview page layout", lambda: gui.control(panel, "captureButton").isVisible())
    shot("02-preview")

    stage("camera page during linkage")
    sequence = "VeryLongProductionSequenceName_" * 10
    gui.page(panel, gui.CAMERA)
    panel.set_camera_display(sequence=sequence, segments=((35, False), (55, True), (30, False)),
                             camera_count=3, frame_start=1, frame_end=120, fps=24,
                             imported_at="2026-11-29 10:00", level="Acceptance_Level",
                             sublevels=("Characters", "Cameras"), stand_in="Acceptance")
    gui.click(panel, "enterLinkageButton")
    yield wait("live linkage with applied camera", lambda:
               controller.linkage_status()["active"] and bool(peer.applied))
    assert "UE" in gui.control(panel, "timeAuthority").text()
    assert not panel.beam.animation_active
    result["elision"]["sequence"] = _elision(panel, "cameraSequence", sequence)
    shot("03-camera")
    gui.click(panel, "endLinkageButton")
    yield wait("linkage resources restored", lambda:
               controller.snapshot()["mode"] == "realtime"
               and not cmds.objExists("MtoU_LinkCamera"))

    stage("complete diagnostics already open, then receive a long failure")
    gui.page(panel, gui.DIAGNOSTIC_PAGE)
    gui.click(panel, "diagnosticsButton")
    details, = gui.windows(gui.DIAGNOSTICS)
    browser = gui.control(details, "diagnosticDetails")
    before = browser.toPlainText()
    assert "此前的连接差异" in before and "FixtureSmile" in before, before
    client = clients[0]
    client.sendall(encode({"type": "error", "kind": "failure", "code": "INVALID_BINDING",
                           "message": "验收时绑定失败", "details": long_detail}))
    # End the peer's output after the error and drain Maya's input until it
    # closes. Closing with unread frames can reset the connection on Windows.
    client.shutdown(socket.SHUT_WR)
    latest = gui.control(panel, "diagnosticLatest")

    def failure_visible():
        state = controller.snapshot()
        result["wire_failure_observed"] = {
            "connection": state["connection"], "code": state["diagnostic"].get("code"),
            "main": latest.toPlainText().splitlines()[:2],
            "details": browser.toPlainText().strip().splitlines()[:2],
        }
        return (state["connection"] == "interrupted"
                and "INVALID_BINDING" in latest.toPlainText()
                and "INVALID_BINDING" in browser.toPlainText())

    yield wait("updated diagnostics panel and already open window", failure_visible)
    peer.stop.set()
    open_text = browser.toPlainText()
    failure = controller.snapshot()["diagnostic"]
    solution = failure["solution"]
    assert failure["code"] == "INVALID_BINDING" and solution, failure
    history_heading = "此前的连接差异"
    current_text, separator, history_text = open_text.partition(history_heading)
    # The diagnostic heading and result must precede the connection metadata.
    assert current_text.lstrip().startswith("[最近一次操作]") and separator, open_text
    assert all(part in current_text for part in (
        "INVALID_BINDING", "验收时绑定失败", solution, "故障详情首行", "故障详情末行")), open_text
    assert "FixtureSmile" in history_text, history_text
    assert all(part in latest.toPlainText() for part in (
        "INVALID_BINDING", solution, "故障详情首行", "故障详情末行")), latest.toPlainText()
    assert latest.wordWrapMode() != qt_compat.QtGui.QTextOption.NoWrap
    scroll_parent = latest.parentWidget()
    while scroll_parent is not None and not isinstance(scroll_parent, QtWidgets.QScrollArea):
        scroll_parent = scroll_parent.parentWidget()
    assert scroll_parent is not None and scroll_parent.verticalScrollBar().maximum() > 0
    assert browser.lineWrapMode() != QtWidgets.QTextEdit.NoWrap
    assert browser.verticalScrollBar().maximum() > 0
    cursor = browser.document().find("故障详情末行")
    assert not cursor.isNull()
    browser.setTextCursor(cursor)
    browser.ensureCursorVisible()
    assert browser.viewport().rect().intersects(browser.cursorRect())
    clipboard = QtWidgets.QApplication.clipboard()
    saved_contents = qt_compat.QtCore.QMimeData()
    original = clipboard.mimeData()
    if original is not None:
        for mime_type in original.formats():
            saved_contents.setData(mime_type, original.data(mime_type))
    try:
        browser.copy()
        assert clipboard.text() == "故障详情末行"
    finally:
        if clipboard.text() == "故障详情末行":
            clipboard.setMimeData(saved_contents)
    browser.moveCursor(qt_compat.QtGui.QTextCursor.Start)

    stage("complete diagnostics opened after the same failure")
    details.close()
    yield wait("prior details destroyed",
               lambda: not gui.windows(gui.DIAGNOSTICS, visible_only=False))
    gui.click(panel, "diagnosticsButton")
    reopened, = gui.windows(gui.DIAGNOSTICS)
    assert gui.control(reopened, "diagnosticDetails").toPlainText() == open_text
    assert len(gui.windows(gui.DIAGNOSTICS, visible_only=False)) == 1
    result["latest_diagnostic"] = {
        "code": failure["code"], "solution": solution, "same_text_reopened": True,
        "wrap_and_scroll": True, "copied_last_line": True,
    }
    shot("04-diagnostic")
    shot("05-complete-diagnostics", reopened)
    assert not peer.errors, peer.errors


def _save_scene(path):
    cmds.file(rename=str(path))
    cmds.file(save=True, type="mayaAscii", force=True)


def _open_scene(path):
    cmds.file(str(path), open=True, force=True, prompt=False)


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scene_settings(evidence, step, result, stage, wait, until_idle, open_panel, close_panel,
                   current_panel):
    a = evidence / "scene-a.ma"
    b = evidence / "scene-b.ma"
    copy = evidence / "scene-a-copy.ma"
    binding, same, missing = (evidence / f"binding-{name}.ma"
                              for name in ("original", "same", "missing"))
    primary = "|binding:Rig|binding:Root"
    display = "|binding:Rig|binding:FixtureDisplay"
    secondary = "|SceneASecondary|Secondary"
    other = "|SceneB|LocalRoot"
    common = {"display": None, "display_attribute": None, "secondary_root": None}
    settings_a = {"workflow": "model", "blendshapes_enabled": False, "show_popups": False,
                  "playback_cap": 24, "custom_range": True, "range_start": 11, "range_end": 37,
                  "root": primary, "secondary_root": secondary, "display": display,
                  "display_attribute": "wardrobe"}
    settings_b = {**common, "workflow": "animation", "blendshapes_enabled": True,
                  "show_popups": True, "playback_cap": 20, "custom_range": True,
                  "range_start": 41, "range_end": 63, "root": other}

    def snapshot():
        return current_panel().controller.snapshot()

    def verify(expected, label):
        settings = current_panel().controller.settings
        observed = {key: getattr(settings, key) for key in expected}
        assert observed == expected, (label, observed, expected)
        assert snapshot()["root"] == expected["root"], (label, snapshot())
        assert current_panel().controller.objects_state()["secondary"] == expected["secondary_root"]
        result.setdefault("settings", {})[label] = observed

    if step.endswith("_write"):
        stage("create disposable rig references and set unnamed scene preferences")
        for path, group in ((binding, "Rig"), (same, "Rig"), (missing, "OtherRig")):
            cmds.file(new=True, force=True)
            _character(group, "Root", "Garment", display=True)
            _save_scene(path)
        cmds.file(new=True, force=True)
        cmds.file(str(binding), reference=True, namespace="binding")
        _character("SceneASecondary", "Secondary", "SecondGarment")
        yield from until_idle("reference attached to unnamed scene")
        panel = current_panel()
        _set_role(panel, primary)
        cmds.select(display, replace=True)
        gui.click(panel, "setDisplayButton")
        _set_secondary(panel, secondary)
        gui.page(panel, gui.PREVIEW)
        gui.set_checked(panel, "cap30", True)
        gui.set_capture_range(panel, 11, 37)
        gui.page(panel, gui.CONNECTION)
        gui.set_checked(panel, "workflowModel", True)
        gui.set_checked(panel, "blendshapesCheck", False)
        gui.page(panel, gui.DIAGNOSTIC_PAGE)
        gui.set_checked(panel, "popupCheck", False)
        gui.page(panel, gui.PREVIEW)
        expected_unnamed = dict(settings_a, playback_cap=30)
        verify(expected_unnamed, "unnamed")

        stage("first save and preference commit leave scene content alone")
        _save_scene(a)
        yield from until_idle("first save")
        a_hash = _hash(a)
        assert not cmds.file(query=True, modified=True)
        gui.set_checked(panel, "cap24", True)
        verify(settings_a, "saved A")
        assert not cmds.file(query=True, modified=True) and _hash(a) == a_hash

        stage("save as preserves current settings")
        _save_scene(copy)
        yield from until_idle("save as")
        verify(settings_a, "saved as A")
        assert _hash(a) == a_hash
        copy_hash = _hash(copy)

        stage("second scene gets its own settings")
        cmds.file(new=True, force=True)
        yield from until_idle("new scene B")
        _character("SceneB", "LocalRoot", "LocalGarment")
        _save_scene(b)
        yield from until_idle("saved B")
        b_hash = _hash(b)
        _set_role(panel, other)
        assert panel.controller.settings.workflow == "animation"
        gui.page(panel, gui.PREVIEW)
        gui.set_checked(panel, "cap20", True)
        gui.set_capture_range(panel, 41, 63)
        verify(settings_b, "saved B")
        assert not cmds.file(query=True, modified=True) and _hash(b) == b_hash

        stage("same process isolates A, B and saved-as settings")
        for path, expected in ((a, settings_a), (b, settings_b), (copy, settings_a)):
            _open_scene(path)
            yield wait("restore " + path.name, lambda: snapshot()["root"] == expected["root"])
            verify(expected, "reopened " + path.name)
        cmds.select(clear=True)
        yield from close_panel()
        yield from open_panel()
        yield wait("restore without Maya selection", lambda: snapshot()["root"] == primary)
        verify(settings_a, "reopened panel")
        assert not cmds.ls(selection=True)
        result["file_hashes"] = {a.name: a_hash, b.name: b_hash, copy.name: copy_hash}
    else:
        import json

        expected_hashes = json.loads(
            (evidence / "scene_settings_write.json").read_text(encoding="utf-8"))["file_hashes"]
        stage("new Maya restores A, B and saved-as settings")
        for path, expected in ((b, settings_b), (copy, settings_a), (a, settings_a)):
            _open_scene(path)
            yield wait("restart restore " + path.name,
                       lambda: snapshot()["root"] == expected["root"])
            verify(expected, "after restart " + path.name)
            assert not cmds.file(query=True, modified=True)

        stage("replace the reference with a rig at the same long path")
        node = cmds.referenceQuery(primary, referenceNode=True)
        old_uuid = cmds.ls(primary, uuid=True)[0]
        cmds.file(str(same), loadReference=node)
        same_modified = cmds.file(query=True, modified=True)
        yield from until_idle("reference replaced")
        yield wait("recover same binding path", lambda: snapshot()["root"] == primary)
        verify(settings_a, "reference with same paths")
        result["reference_uuid_changed"] = cmds.ls(primary, uuid=True)[0] != old_uuid
        assert result["reference_uuid_changed"]
        assert cmds.file(query=True, modified=True) == same_modified

        stage("a duplicate short name in a different hierarchy requires resetting")
        cmds.file(str(missing), loadReference=node)
        missing_modified = cmds.file(query=True, modified=True)
        yield from until_idle("missing path")
        yield wait("diagnostic for missing saved path", lambda:
                   (snapshot()["diagnostic"] or {}).get("code") == "PREFERENCES_ROLE_MISSING")
        assert cmds.objExists("|binding:OtherRig|binding:Root") and not cmds.objExists(primary)
        assert snapshot()["root"] is None
        assert current_panel().controller.settings.root == primary
        error_text = str(snapshot()["diagnostic"])
        assert primary in error_text, error_text
        gui.page(current_panel(), gui.DIAGNOSTIC_PAGE)
        assert "PREFERENCES_ROLE_MISSING" in gui.control(
            current_panel(), "diagnosticLatest").toPlainText()
        assert cmds.file(query=True, modified=True) == missing_modified

        stage("reopen original saved scene after unsaved reference replacement")
        _open_scene(a)
        yield wait("original binding recovery", lambda: snapshot()["root"] == primary)
        verify(settings_a, "original reference recovered")
        assert not cmds.file(query=True, modified=True)
        result["file_hashes"] = expected_hashes

    assert {path.name: _hash(path) for path in (a, b, copy)} == result["file_hashes"]
