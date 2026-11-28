# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Shared diagnostic formatting and the copyable detail window."""

from datetime import datetime
from html import escape

from ..diagnostics import (diagnostic_parts, linkage_diagnostic_text,
                           object_set_diagnostic_text, realtime_diagnostic_text)
from ..strings import text
from ..qt_compat import QtCore, QtGui, QtWidgets
from . import style
from .widgets import label, named, row


def metric_value(value, unit="", digits=1):
    return "—" if value is None else "{0:.{1}f}{2}".format(value, digits, unit)


def _section(title, content):
    return ('<p style="margin: 10px 0 0"><span style="color:#9AA1A9">[{0}]</span> '
            '{1}</p>').format(escape(title), escape(content).replace("\n", "<br>"))


def _versions(state):
    value = state["versions"]
    result = state["diagnostic"]
    remote = (result.get("versions") or {}).get("unreal", {})
    ue_plugin = remote.get("plugin_version") or value["ue_plugin"]
    ue_protocol = remote.get("protocol_version") or value["ue_protocol"]
    return (text("connection_versions")).format(
                value["maya_host"] or "—", value["maya_plugin"], value["maya_protocol"],
                value["ue_host"] or "—", ue_plugin or "—", ue_protocol or "—")


def _performance(state):
    metrics = state["metrics"]
    maya = metrics.get("stages", {}).get("sample_total", {}).get("latest_ms")
    return (text("detailed_performance")).format(
                metric_value(maya, " ms"), metric_value(metrics.get("sent_hz"), " Hz", 0),
                metric_value(metrics.get("replaced_frames"), text("frame_unit"), 0),
                metric_value(metrics.get("ue_apply_ms"), " ms"),
                metric_value(metrics.get("ue_editor_fps"), " fps", 0))


class DiagnosticWindow(QtWidgets.QDialog):
    def __init__(self, parent, on_close):
        super().__init__(parent)
        self._on_close = on_close
        self._last_html = None
        self._recent = None
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowFlags((self.windowFlags() | QtCore.Qt.WindowMinMaxButtonsHint)
                            & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(text("full_diagnostics_title"))
        self.resize(620, 440)
        self.setMinimumSize(370, 250)
        self.setStyleSheet(style.STYLESHEET)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QtWidgets.QFrame()
        header.setProperty("role", "header")
        self._update_time = label(role="chip")
        line = row(label(text("full_diagnostics_title"), role="large"), None, self._update_time)
        line.setContentsMargins(16, 9, 16, 9)
        header.setLayout(line)
        layout.addWidget(header)
        self._text = named(QtWidgets.QTextBrowser(), "diagnosticDetails")
        self._text.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self._text.document().setDocumentMargin(15)
        layout.addWidget(self._text, 1)

    def refresh(self, state, camera):
        diagnosis = state["diagnostic"]
        if self._recent != diagnosis:
            self._recent = diagnosis
            self._update_time.setText(text("details_update_time").format(datetime.now().strftime("%H:%M:%S")))
        title, message = diagnostic_parts(diagnosis)
        background, foreground = {
            "failure": ("#3A2A2A", "#FFB4AB"),
            "warning": ("#3B3228", "#F2B84B"),
        }.get(diagnosis.get("outcome"), ("#2F3236", "#E6E8EA"))
        recent = (text("recent_operation_card")).format(background, foreground, escape(title),
                                          "<br>" + escape(message).replace("\n", "<br>") if message else "")
        character = text("diagnostic_character").format(
            state["bone_count"], state["curve_count"], state["outfit"] or "—",
            metric_value(state["fps"], " fps", 2).replace(".00 ", " "))
        if state["root"]:
            character += "\n" + state["root"]
        if state["secondary"]:
            character += "\n" + state["objects"]["secondary_detail"]
        character += "\n" + object_set_diagnostic_text(state["objects"])
        camera_summary = " · ".join(filter(None, (
            camera.get("sequence"),
            text("diagnostic_camera_count").format(camera["camera_count"]) if camera.get("camera_count") is not None else "",
            text("last_camera_import").format(camera["imported_at"]) if camera.get("imported_at") else "",
            text("camera_updated") if any(flag for _, flag in camera.get("segments", ())) else "",
        )))
        difference = state["connection_difference"] or {}
        difference_text = "\n".join(filter(None, (difference.get("code"), difference.get("details"))))
        parts = [
            recent,
            _section(text("connect"), _versions(state)),
            _section(text("character"), character),
            _section(text("performance"), _performance(state)),
            _section(text("camera"), camera_summary or text("camera_list_empty")),
            _section(text("previous_difference"), difference_text or text("no_connection_difference")),
            _section(text("sequence_linkage"), linkage_diagnostic_text(state["linkage"])),
        ]
        performance = realtime_diagnostic_text(state["metrics"])
        if performance:
            parts.append(_section(text("sampling_details"), performance))
        html = '<div style="color:#E6E8EA; line-height:145%">{0}</div>'.format("".join(parts))
        if self._last_html != html:
            self._last_html = html
            self._replace_text(html)

    def _replace_text(self, html):
        browser = self._text
        cursor = browser.textCursor()
        anchor, position = cursor.anchor(), cursor.position()
        selection = cursor.selectedText()
        scrollbar = browser.verticalScrollBar()
        top = scrollbar.value()
        browser.setHtml(html)
        limit = browser.document().characterCount() - 1
        if max(anchor, position) <= limit:
            cursor = browser.textCursor()
            cursor.setPosition(anchor)
            cursor.setPosition(position, QtGui.QTextCursor.KeepAnchor)
            if cursor.selectedText() == selection:
                browser.setTextCursor(cursor)
            elif selection:
                matching = browser.document().find(selection)
                if not matching.isNull():
                    browser.setTextCursor(matching)
        scrollbar.setValue(top)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._on_close(self)
        super().closeEvent(event)
