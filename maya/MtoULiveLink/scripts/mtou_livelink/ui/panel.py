# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Four-page Maya window. All operations are supplied by the controller."""

from .. import qt_compat
from ..config import CACHED_MODE, CONTROL_UNREAL, LINKAGE_MODE, PLAYBACK_CAP_CHOICES
from ..config import REALTIME_MODE, WORKFLOW_ANIMATION, WORKFLOW_MODEL, __version__
from ..diagnostics import cache_summary_text, diagnostic_dialog_text, diagnostic_result
from ..strings import LEGAL_TITLE, legal_text, text
from ..qt_compat import QtCore, QtGui, QtWidgets
from . import style
from .details import DiagnosticWindow, metric_value
from .widgets import (
    CameraStrip, ElidedLabel, LinkBeam, PerformanceBar, Segments, WrappingNote, button, card,
    label, named, numeric, refresh_style, row, scroll_page, section,
)

Qt = QtCore.Qt
WINDOW_NAME = "MtoULiveLinkWindow"
DIAGNOSTIC_WINDOW_NAME = "MtoULiveLinkDiagnosticWindow"


class PanelWindow(QtWidgets.QDialog):
    def __init__(self, controller, on_close, parent=None):
        super().__init__(parent)
        self.setObjectName(WINDOW_NAME)
        self.setWindowTitle("MtoU Live Link")
        self.setWindowFlags((self.windowFlags() | Qt.WindowMinMaxButtonsHint)
                            & ~Qt.WindowContextHelpButtonHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_AlwaysShowToolTips)
        self.resize(400, 680)
        self.setMinimumSize(400, 420)
        self.controller = controller
        self._on_close = on_close
        self._details = None
        self._message_boxes = set()
        self._closing = False
        self._camera = {}
        self._recent_data = None
        self.setStyleSheet(style.STYLESHEET)
        font = QtGui.QFont("Microsoft YaHei UI")
        font.setPixelSize(13)
        font.setStyleStrategy(QtGui.QFont.PreferAntialias)
        self.setFont(font)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())
        tabs = named(QtWidgets.QTabWidget(), "panelTabs")
        tabs.setDocumentMode(True)
        tabs.setUsesScrollButtons(False)
        tabs.tabBar().setExpanding(True)
        layout.addWidget(tabs, 1)
        for title, build in (
                (text("connect"), self._build_connection), (text("preview"), self._build_preview),
                (text("camera"), self._build_camera), (text("diagnostics"), self._build_diagnostics)):
            scroll, page = scroll_page()
            build(page)
            page.addStretch()
            tabs.addTab(scroll, title)

        self._unsubscribe = controller.subscribe(self.render)
        self._refresh = QtCore.QTimer(self)
        self._refresh.setInterval(350)
        self._refresh.timeout.connect(self._render_current)
        self._refresh.start()

    def start(self):
        self.controller.start(self._interact, self.close)
        self._render_current()

    def _build_header(self):
        head = QtWidgets.QFrame()
        head.setProperty("role", "header")
        content = QtWidgets.QVBoxLayout(head)
        content.setContentsMargins(16, 11, 16, 11)
        content.setSpacing(8)

        self._connection_status = label(name="connectionStatus")
        self._authority = label(name="timeAuthority", tone="dim")
        content.addLayout(row(label("MTOU LIVE LINK", role="wordmark"), None,
                              self._connection_status, self._authority))

        self._from, self._toward = label("M"), label("U")
        for mark, tip in ((self._from, "Maya"), (self._toward, "Unreal")):
            mark.setFixedSize(38, 38)
            mark.setAlignment(Qt.AlignCenter)
            mark.setToolTip(tip)

        self.beam = LinkBeam()
        self._beam_rate = numeric(label(name="beamRate"), 21)
        self._beam_rate.setAlignment(Qt.AlignCenter)
        self._beam_rate.setMinimumWidth(28)
        self._beam_unit = label("Hz", tone="dim")
        self._beam_unit.setAlignment(Qt.AlignVCenter)
        readout = QtWidgets.QFrame()
        readout.setStyleSheet("QFrame { background: #1C1E20; border: 0; }")
        readout_layout = row(self._beam_rate, self._beam_unit, spacing=4)
        readout_layout.setContentsMargins(7, 0, 7, 0)
        readout.setLayout(readout_layout)
        holder = QtWidgets.QWidget()
        stack = QtWidgets.QStackedLayout(holder)
        stack.setStackingMode(QtWidgets.QStackedLayout.StackAll)
        stack.addWidget(self.beam)
        overlay = QtWidgets.QWidget()
        overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        overlay_layout = row(None, readout, None)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay.setLayout(overlay_layout)
        stack.addWidget(overlay)
        stack.setCurrentWidget(overlay)
        content.addLayout(row(self._from, holder, self._toward, spacing=10))

        self._character_line = ElidedLabel("currentCharacter", middle=True)
        self._scene_frame = numeric(label(name="sceneFrame", role="chip"))
        self._scene_frame.setFixedWidth(82)
        self._scene_frame.setAlignment(Qt.AlignCenter)
        self._connect = button(text("connect"), "connectButton", self._toggle_connection, "ghost")
        content.addLayout(row(self._character_line, self._scene_frame, self._connect))
        return head

    def _build_connection(self, page):
        c = self.controller
        self._workflow = Segments(
            ((WORKFLOW_ANIMATION, "workflowAnimation", text("animation")),
             (WORKFLOW_MODEL, "workflowModel", text("model"))), c.set_workflow)
        page.addLayout(row(label(text("workflow"), tone="dim"), self._workflow, None))
        page.addLayout(section(text("character")))

        frame, character = card()
        self._character = ElidedLabel("characterName", role="large")
        self._outfit = ElidedLabel("outfitName", role="chip", middle=True)
        self._outfit.setMaximumWidth(116)
        self._set_role = button(text("set_role"), "setRoleButton", c.set_role, "ghost")
        name_line = row(self._character, self._outfit, self._set_role)
        name_line.setStretch(0, 4)
        name_line.setStretch(1, 4)
        character.addLayout(name_line)
        self._root = numeric(ElidedLabel("rootPath", middle=True, tone="dim"), 12)
        character.addWidget(self._root)

        values = row()
        self._stats = {}
        for key, title in (("bone_count", text("bones")), ("curve_count", "BlendShape"),
                           ("mesh_count", text("outfit_meshes"))):
            tile, tile_content = card("stat", spacing=2)
            tile_content.setContentsMargins(9, 5, 9, 6)
            counter = numeric(label("0", name=key, role="large"), 17)
            tile_content.addWidget(counter)
            tile_content.addWidget(label(title, tone="dim"))
            self._stats[key] = counter
            values.addWidget(tile, 1)
        character.addLayout(values)
        self._select_display = button(text("choose_display"), "setDisplayButton",
                                      c.set_display_controller, "ghost")
        self._select_duplicates = button(text("select_duplicates"), "duplicateBonesButton",
                                         c.select_duplicate_bones, "ghost")
        character.addLayout(row(self._select_display, self._select_duplicates, None))
        page.addWidget(frame)

        self._blendshapes = self._checkbox(text("pass_bs"), "blendshapesCheck")
        self._blendshapes.toggled.connect(c.set_blendshapes)
        self._bs_explanation = label(tone="dim", wrap=True)
        explanation = row(self._blendshapes, self._bs_explanation)
        explanation.setStretch(1, 1)
        page.addLayout(explanation)
        page.addLayout(section(text("secondary")))
        self._secondary_frame, secondary = card("empty")
        self._secondary_empty = label(text("secondary_empty"),
                                      tone="dim", wrap=True)
        self._secondary_path = ElidedLabel("secondaryRoot", middle=True)
        self._secondary_note = label(tone="dim", wrap=True)
        self._set_secondary = button(text("set_secondary"), "setSecondaryButton", c.set_secondary_object)
        self._remove_secondary = button(text("remove_secondary"), "removeSecondaryButton",
                                        c.remove_secondary_object)
        secondary.addLayout(row(self._secondary_empty, self._secondary_path,
                                self._set_secondary, self._remove_secondary))
        secondary.addWidget(self._secondary_note)
        page.addWidget(self._secondary_frame)

    def _build_preview(self, page):
        c = self.controller
        self._mode = Segments(
            ((REALTIME_MODE, "realtimeMode", text("realtime")),
             (CACHED_MODE, "cachedMode", text("cached_playback"))), c.set_mode)
        page.addLayout(row(self._key(text("mode")), self._mode, None))
        self._cap = Segments(
            tuple((cap, "cap{0}".format(cap), str(cap)) for cap in PLAYBACK_CAP_CHOICES),
            c.set_playback_cap, monospace=True)
        page.addLayout(row(self._key(text("playback_cap")), self._cap, label("fps", tone="dim"), None))
        page.addLayout(section(text("cache")))

        frame, cache = card()
        self._range = Segments(
            ((False, "rangePlayback", text("playback_range")), (True, "rangeCustom", text("custom_range"))),
            c.set_capture_range)
        cache.addLayout(row(self._key(text("capture_range")), self._range, None))
        self._range_start = self._frame_field("rangeStart", text("start_frame"), True)
        self._range_end = self._frame_field("rangeEnd", text("end_frame"), False)
        self._frames_count = label(tone="dim", wrap=True)
        self._frames_count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        cache.addLayout(row(self._key(text("frame")), self._range_start, label(text("until"), tone="dim"),
                            self._range_end, self._frames_count))
        self._cache_progress = named(QtWidgets.QProgressBar(), "cacheProgress")
        self._cache_progress.setRange(0, 100)
        self._cache_progress.setTextVisible(False)
        cache.addWidget(self._cache_progress)
        self._cache_status = named(label(tone="dim", wrap=True), "cacheStatus")
        self._capture = button(text("capture_upload"), "captureButton", c.capture_cache, "primary")
        self._upload = button(text("upload_retained"), "uploadButton", c.upload_cache)
        self._cancel = button(text("cancel_capture"), "cancelCaptureButton", c.cancel_capture)
        cache.addLayout(row(self._cache_status, self._capture))
        extra = row(None, self._cancel, self._upload)
        cache.addLayout(extra)
        page.addWidget(frame)
        page.addWidget(label(text("cache_in_unreal"), tone="dim", wrap=True))

    def _build_camera(self, page):
        c = self.controller
        page.addLayout(section(text("ue_camera")))
        frame, camera = card()
        self._sequence = ElidedLabel("cameraSequence", middle=True)
        self._camera_updated = label(text("camera_updated"), role="chip", tone="amber")
        self._camera_updated.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self._camera_updated.hide()
        title_line = row(self._sequence, self._camera_updated)
        title_line.setStretch(0, 1)
        camera.addLayout(title_line)
        self._cuts = CameraStrip()
        camera.addWidget(self._cuts)
        self._camera_range = label(tone="dim", wrap=True)
        self._camera_imported = label(tone="dim")
        camera.addLayout(row(self._camera_range, self._camera_imported))
        update = button(text("update_camera"), "updateCameraButton", None, "primary")
        import_camera = button(text("import_camera"), "importCameraButton", None, "ghost")
        for control in (update, import_camera):
            self._enable(control, (False, text("camera_import_unavailable")))
        camera.addLayout(row(update, import_camera, None))
        page.addWidget(frame)

        page.addLayout(section(text("sequence_linkage")))
        frame, linkage = card()
        self._level = ElidedLabel("unrealLevel", middle=True)
        self._sublevels = ElidedLabel("unrealSublevels")
        self._time_control = label(tone="dim", wrap=True)
        self._stand_in = ElidedLabel("unrealStandIn", middle=True)
        for title, widget in ((text("level"), self._level), (text("sublevels"), self._sublevels),
                              (text("time"), self._time_control), (text("stand_in"), self._stand_in)):
            line = row(self._key(title), widget)
            line.setStretch(1, 1)
            linkage.addLayout(line)
        page.addWidget(frame)
        self._link_status = ElidedLabel("linkageStatus", tone="dim")
        page.addWidget(self._link_status)
        self._enter_linkage = button(text("start_linkage"), "enterLinkageButton", c.enter_linkage)
        self._leave_linkage = button(text("return_realtime"), "leaveLinkageButton",
                                     lambda: c.leave_linkage("realtime"))
        self._end_linkage = button(text("end_linkage"), "endLinkageButton", lambda: c.leave_linkage("end"))
        page.addLayout(row(None, self._enter_linkage, self._leave_linkage, self._end_linkage))

    def _build_diagnostics(self, page):
        page.addLayout(section(text("latest_operation")))
        self._recent_card, recent = card()
        self._recent = named(WrappingNote(), "diagnosticLatest")
        recent.addWidget(self._recent)
        page.addWidget(self._recent_card)

        page.addLayout(section(text("performance")))
        frame, performance = card()
        self._perf = []
        for name in (text("maya_sample"), text("ue_apply")):
            meter = PerformanceBar()
            value = numeric(label("—"), 12)
            value.setMinimumWidth(68)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            performance.addLayout(row(self._key(name), meter, value))
            self._perf.append((meter, value))
        self._perf_extra = label(tone="dim", wrap=True)
        self._perf_extra.setToolTip(text("metric_help"))
        performance.addWidget(self._perf_extra)
        page.addWidget(frame)

        page.addLayout(section(text("previous_difference")))
        self._difference = ElidedLabel("connectionDifference", tone="dim")
        page.addWidget(self._difference)
        page.addStretch()
        details = button(text("full_diagnostics"), "diagnosticsButton", self.show_diagnostics)
        self._popups = self._checkbox(text("popup_on_error"), "popupCheck")
        self._popups.setToolTip(text("popup_help"))
        self._popups.toggled.connect(self.controller.set_popups)
        page.addLayout(row(details, None, self._popups))
        page.addLayout(row(None, button(text("about"), "aboutButton", self._show_legal, "ghost")))

    def _checkbox(self, title, name):
        widget = named(QtWidgets.QCheckBox(title), name)
        checkbox_style = QtWidgets.QStyleFactory.create("Fusion")
        checkbox_style.setParent(widget)
        widget.setStyle(checkbox_style)
        palette = widget.palette()
        palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(style.MINT))
        palette.setColor(QtGui.QPalette.Base, QtGui.QColor(style.HEADER))
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor(style.BACKGROUND))
        palette.setColor(QtGui.QPalette.Button, QtGui.QColor(style.CARD))
        palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor(style.TEXT))
        palette.setColor(QtGui.QPalette.Text, QtGui.QColor(style.MINT))
        widget.setPalette(palette)
        return widget

    @staticmethod
    def _key(value):
        widget = label(value, tone="dim")
        widget.setMinimumWidth(62)
        return widget

    def _frame_field(self, name, accessible_name, start):
        field = numeric(named(QtWidgets.QSpinBox(), name))
        field.setRange(-2147483647, 2147483647)
        field.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        field.setFixedWidth(70)
        field.setAlignment(Qt.AlignRight)
        field.setKeyboardTracking(False)
        field.setAccessibleName(accessible_name)
        field.editingFinished.connect(
            lambda: self.controller.set_capture_range(True, **{
                "start" if start else "end": field.value()}))
        return field

    @staticmethod
    def _enable(widget, action):
        enabled, reason = action
        widget.setEnabled(enabled)
        widget.setToolTip(reason if not enabled else "")
        widget.setAttribute(Qt.WA_AlwaysShowToolTips)

    @staticmethod
    def _check(widget, checked):
        blocked = widget.blockSignals(True)
        widget.setChecked(checked)
        widget.blockSignals(blocked)

    def _toggle_connection(self):
        if self.controller.actions()["disconnecting"][0]:
            self.controller.disconnect()
        else:
            self.controller.connect()

    def _render_current(self):
        if not self._closing:
            self.render(self.controller.snapshot())

    def render(self, state):
        if self._closing:
            return
        self._render_header(state)
        self._render_connection(state)
        self._render_preview(state)
        self._render_camera(state)
        self._render_diagnostics(state)
        if self._details is not None:
            self._details.refresh(state, self._camera)

    def _render_header(self, state):
        connection = state["connection"]
        mode = state["mode"]
        badge, tone = {
            "idle": (text("disconnected"), "dim"),
            "connecting": (text("connecting"), "dim"),
            "connected": (text("connected"), "mint"),
            "reconnecting": (text("reconnecting"), "amber"),
            "interrupted": (text("interrupted"), "coral"),
        }[connection]
        self._connection_status.setText("●  " + badge)
        refresh_style(self._connection_status, "tone", tone)
        self._connection_status.setToolTip(state["status"])
        driving = state["authority"]
        time_mode = {
            REALTIME_MODE: text("realtime"),
            CACHED_MODE: text("cached_playback"),
            LINKAGE_MODE: text("sequence_linkage"),
        }[mode]
        self._authority.setText((time_mode + " · " + (text("ue_controls_time") if driving == CONTROL_UNREAL
                                else text("maya_controls_time"))) if connection == "connected"
                                and mode == LINKAGE_MODE else time_mode if connection == "connected" else "")
        self._authority.setToolTip(state["status"])
        self.beam.set_state(connection, driving)
        for mark, controlled in ((self._from, driving != CONTROL_UNREAL),
                                 (self._toward, driving == CONTROL_UNREAL)):
            active = controlled and connection == "connected"
            color = style.MINT if active else style.DIM
            outline = color if active and mode != REALTIME_MODE else style.BORDER
            mark_style = "QLabel {{ color:{0}; background:{1}; border:1px solid {2}; "                 "border-radius:10px; font-size:15px; font-weight:bold; }}".format(
                    color, style.BACKGROUND, outline)
            if mark.styleSheet() != mark_style:
                mark.setStyleSheet(mark_style)
        if mode == CACHED_MODE or mode == LINKAGE_MODE:
            self._beam_rate.setText(text("ue_playback") if mode == CACHED_MODE else
                                    text("ue_controls_time") if driving == CONTROL_UNREAL else text("maya_controls_time"))
            numeric(self._beam_rate, 15)
            self._beam_unit.hide()
        else:
            self._beam_rate.setText(metric_value(state["metrics"].get("sent_hz"), digits=0))
            numeric(self._beam_rate, 21)
            self._beam_unit.show()

        root = state["root"]
        character = self._character_name(root)
        self._character_line.set_full_text(
            "{0} · {1}".format(character, state["outfit"]) if root and state["outfit"]
            else character if root else text("no_character"))
        value = state["frame"] if mode == LINKAGE_MODE else state["fps"]
        self._scene_frame.setText((text("current_frame") if mode == LINKAGE_MODE else "{0:g} fps").format(value)
                                  if value is not None else "—")
        self._connect.setText(text("disconnect") if state["actions"]["disconnecting"][0] else text("connect"))

    @staticmethod
    def _character_name(root):
        if not root:
            return text("not_configured")
        leaf = root.rsplit("|", 1)[-1]
        return leaf.split(":", 1)[0] if ":" in leaf else leaf

    def _render_connection(self, state):
        root = state["root"]
        secondary = state["secondary"]
        settings = state["settings"]
        actions = state["actions"]
        self._workflow.select(settings.workflow)
        self._character.set_full_text(self._character_name(root))
        self._outfit.set_full_text(state["outfit"] or "—")
        refresh_style(self._outfit, "tone", "mint" if state["outfit"] else "dim")
        self._set_role.setText(text("reset_role") if root else text("set_role"))
        self._root.set_full_text(root or text("root_unselected"))
        for key, tile in self._stats.items():
            tile.setText("{0:,}".format(state[key]))
        self._select_duplicates.setVisible(bool(state["duplicates"]))
        if state["duplicates"]:
            self._select_duplicates.setText(text("select_duplicate_count").format(len(state["duplicates"])))
        self._enable(self._select_display, actions["set_display"])
        self._enable(self._select_duplicates, actions["select_duplicates"])
        self._check(self._blendshapes, settings.blendshapes_enabled or settings.workflow == WORKFLOW_ANIMATION)
        self._enable(self._blendshapes, actions["blendshapes"])
        self._bs_explanation.setText(text("bs_explanation")
                                     if settings.workflow == WORKFLOW_MODEL
                                     else text("bs_always"))
        self._secondary_path.set_full_text(secondary or "")
        self._secondary_path.setVisible(bool(secondary))
        self._secondary_note.setVisible(bool(secondary))
        self._secondary_note.setText(state["objects"]["state"])
        self._secondary_empty.setVisible(not secondary)
        self._set_secondary.setVisible(not secondary)
        self._remove_secondary.setVisible(bool(secondary))
        self._enable(self._set_secondary, actions["set_secondary"])
        self._enable(self._remove_secondary, actions["remove_secondary"])
        refresh_style(self._secondary_frame, "role", "card" if secondary else "empty")

    def _render_preview(self, state):
        settings, actions = state["settings"], state["actions"]
        self._mode.select(state["mode"])
        self._cap.select(settings.playback_cap)
        self._range.select(settings.custom_range)
        self._enable(self._mode.buttons[REALTIME_MODE], actions["realtime"])
        self._enable(self._mode.buttons[CACHED_MODE], actions["cached"])
        for option in self._cap.buttons.values():
            self._enable(option, actions["cap"])
        for option in self._range.buttons.values():
            self._enable(option, actions["range"])
        start, end = state["frame_range"]
        for field, value in ((self._range_start, start), (self._range_end, end)):
            self._enable(field, actions["range"] if settings.custom_range else
                         (False, text("custom_range_help")))
            if not field.hasFocus():
                field.setValue(int(value))
        count = max(0, int(end) - int(start) + 1)
        self._frames_count.setText(text("frames_count").format(count))
        view = state["cache"]
        progress = int(100 * view.current / view.total) if view and view.total else 0
        self._cache_progress.setValue(progress)
        summary = cache_summary_text(view.cache_summary) if view else ""
        self._cache_status.setToolTip(summary)
        self._cache_status.setText(state["cache_status"] or text("cache_empty"))
        for widget, action in ((self._capture, "capture"), (self._upload, "upload"),
                               (self._cancel, "cancel")):
            self._enable(widget, actions[action])
            if action != "capture":
                widget.setVisible(actions[action][0])

    def set_camera_display(self, *, sequence=None, segments=(), camera_count=None,
                           frame_start=None, frame_end=None, fps=None, imported_at=None,
                           level=None, sublevels=(), stand_in=None):
        """Display read-only camera and level information supplied by the host."""
        self._camera = {
            "sequence": sequence, "segments": tuple(segments), "camera_count": camera_count,
            "frame_start": frame_start, "frame_end": frame_end, "fps": fps,
            "imported_at": imported_at, "level": level,
            "sublevels": tuple(sublevels), "stand_in": stand_in,
        }
        self._render_current()

    def _render_camera(self, state):
        camera = self._camera
        self._sequence.set_full_text(camera.get("sequence") or text("camera_list_empty"))
        self._cuts.set_segments(camera.get("segments", ()))
        self._camera_updated.setVisible(any(updated for _, updated in camera.get("segments", ())))
        self._camera_imported.setText(text("last_camera_import").format(camera["imported_at"])
                                      if camera.get("imported_at") else "")
        if camera.get("camera_count") is not None:
            self._camera_range.setText(text("camera_range").format(
                camera["camera_count"], camera["frame_start"], camera["frame_end"], camera["fps"]))
        else:
            self._camera_range.setText(text("camera_import_unavailable"))
        self._level.set_full_text(camera.get("level") or text("level_unavailable"))
        self._sublevels.set_full_text("、".join(camera.get("sublevels", ())) or "—")
        self._stand_in.set_full_text(camera.get("stand_in") or text("stand_in_unavailable"))
        active = state["mode"] == LINKAGE_MODE
        self._time_control.setText(
            (text("camera_maya_follows") if state["authority"] == CONTROL_UNREAL
             else text("camera_ue_follows")) if active else text("camera_linkage_idle"))
        self._link_status.set_full_text(state["linkage"]["line"])
        self._link_status.setToolTip(state["linkage"]["line_tooltip"])
        for widget, name in ((self._enter_linkage, "enter_linkage"), (self._leave_linkage, "leave_linkage"),
                             (self._end_linkage, "end_linkage")):
            self._enable(widget, state["actions"][name])

    def _render_diagnostics(self, state):
        diagnosis = state["diagnostic"]
        if self._recent_data != diagnosis:
            self._recent_data = diagnosis
            self._recent.setPlainText(diagnostic_result(diagnosis))
            refresh_style(self._recent_card, "severity", diagnosis.get("outcome", ""))
        difference = state["connection_difference"]
        self._difference.set_full_text(
            difference["difference_summary"] if difference else text("no_connection_difference"))
        metrics = state["metrics"]
        budget = 1000.0 / state["fps"] if state["fps"] else None
        maya = metrics.get("stages", {}).get("sample_total", {})
        for (meter, field), ms in zip(self._perf, (maya.get("latest_ms"), metrics.get("ue_apply_ms"))):
            field.setText(metric_value(ms, " ms"))
            meter.set_measurement(ms, budget)
        self._perf_extra.setText(text("quick_performance").format(
            metric_value(metrics.get("sent_hz"), " Hz", 0),
            metric_value(metrics.get("replaced_frames"), text("frame_unit"), 0),
            metric_value(metrics.get("ue_editor_fps"), " fps", 0), metric_value(budget, " ms")))
        self._check(self._popups, state["settings"].show_popups)

    def show_diagnostics(self):
        if self._details is None:
            self._details = DiagnosticWindow(self, self._details_closed)
            self._details.setObjectName(DIAGNOSTIC_WINDOW_NAME)
        self._details.refresh(self.controller.snapshot(), self._camera)
        self._details.show()
        self._details.raise_()
        self._details.activateWindow()
        return self._details

    def _details_closed(self, window):
        if self._details is window:
            self._details = None

    def _show_legal(self):
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle(LEGAL_TITLE)
        dialog.setText(legal_text(__version__))
        dialog.addButton(text("close"), QtWidgets.QMessageBox.AcceptRole)
        qt_compat.dialog_exec(dialog)

    def _interact(self, kind, data):
        if kind == "choose_enum":
            return self._choose_outfit_attribute(data)
        if kind == "large_cache":
            size, total = data
            dialog = QtWidgets.QMessageBox(self)
            dialog.setWindowTitle(text("confirm_large_cache"))
            dialog.setText(text("large_cache_size").format(size / float(1 << 30), total))
            proceed = dialog.addButton(text("continue"), QtWidgets.QMessageBox.AcceptRole)
            cancel = dialog.addButton(text("cancel"), QtWidgets.QMessageBox.RejectRole)
            dialog.setDefaultButton(proceed)
            dialog.setEscapeButton(cancel)
            qt_compat.dialog_exec(dialog)
            return dialog.clickedButton() is proceed
        self._show_popup(kind, data)
        return None

    def _choose_outfit_attribute(self, candidates):
        if len(candidates) > 5:
            dialog = QtWidgets.QInputDialog(self)
            dialog.setWindowTitle(text("choose_outfit_attribute"))
            dialog.setLabelText(text("multiple_outfit_attributes"))
            dialog.setComboBoxItems(candidates)
            dialog.setOkButtonText(text("ok"))
            dialog.setCancelButtonText(text("cancel"))
            return dialog.textValue() if qt_compat.dialog_exec(dialog) else None

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(text("choose_outfit_attribute"))
        dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(label(text("multiple_outfit_attributes")))
        chosen = candidates[0]

        def choose(value):
            nonlocal chosen
            chosen = value

        options = Segments(
            [(value, "", value) for value in candidates], choose)
        options.select(chosen)
        layout.addWidget(options)
        buttons = QtWidgets.QDialogButtonBox()
        buttons.addButton(text("ok"), QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton(text("cancel"), QtWidgets.QDialogButtonBox.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return chosen if qt_compat.dialog_exec(dialog) else None

    def _show_popup(self, kind, diagnostic):
        info = QtWidgets.QMessageBox(self)
        info.setWindowTitle(text("connection_warning") if kind == "warning" else text("connection_problem"))
        info.setIcon(QtWidgets.QMessageBox.Warning)
        info.setTextFormat(Qt.PlainText)
        info.setText(diagnostic_dialog_text(diagnostic))
        info.setProperty("diagnosticCode", diagnostic.get("code"))
        info.setAttribute(Qt.WA_DeleteOnClose)
        details = info.addButton(text("view_full_diagnostics"), QtWidgets.QMessageBox.ActionRole)
        details.clicked.connect(self.show_diagnostics)
        if diagnostic.get("duplicate_paths"):
            select = info.addButton(text("select_duplicates"), QtWidgets.QMessageBox.ActionRole)
            select.clicked.connect(self.controller.select_duplicate_bones)
        accept = info.addButton(text("ok"), QtWidgets.QMessageBox.AcceptRole)
        info.setDefaultButton(accept)
        info.setEscapeButton(accept)
        self._message_boxes.add(info)
        info.finished.connect(lambda _: self._message_boxes.discard(info))
        info.open()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if not self._closing:
            self._closing = True
            self._refresh.stop()
            if self._details is not None:
                self._details.close()
            for popup in tuple(self._message_boxes):
                popup.close()
            self._unsubscribe()
            self.controller.close()
            self._on_close(self)
        super().closeEvent(event)
