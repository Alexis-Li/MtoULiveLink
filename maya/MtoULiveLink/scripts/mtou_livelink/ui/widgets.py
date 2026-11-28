# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Native panel widget helpers and its three painted indicators."""

from .. import qt_compat
from ..qt_compat import QtCore, QtGui, QtWidgets
from . import style

Qt = QtCore.Qt


def refresh_style(widget, name, value):
    if widget.property(name) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()


def named(widget, name):
    if name:
        widget.setObjectName(name)
    return widget


def numeric(widget, size=None):
    font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
    font.setPixelSize(size or 13)
    widget.setFont(font)
    return widget


def label(value="", name=None, role=None, tone=None, wrap=False):
    widget = named(QtWidgets.QLabel(value), name)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(wrap)
    if role:
        widget.setProperty("role", role)
    if tone:
        widget.setProperty("tone", tone)
    return widget


class ElidedLabel(QtWidgets.QLabel):
    def __init__(self, name=None, *, middle=False, role=None, tone=None):
        super().__init__()
        self._full_text = ""
        self._mode = Qt.ElideMiddle if middle else Qt.ElideRight
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        named(self, name)
        if role:
            self.setProperty("role", role)
        if tone:
            self.setProperty("tone", tone)

    def set_full_text(self, text):
        if text != self._full_text:
            self._full_text = text
            self.setToolTip(text)
            self.setAccessibleName(text)
            self._update_text()

    def _update_text(self):
        bounds = self.contentsRect()
        padding = 15 if self.property("role") == "chip" else 0
        text = self.fontMetrics().elidedText(self._full_text, self._mode, bounds.width() - padding)
        self.setText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_text()


def button(text, name, action, role=None):
    widget = named(QtWidgets.QPushButton(text), name)
    widget.setAutoDefault(False)
    widget.setDefault(False)
    widget.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
    if role:
        widget.setProperty("role", role)
    if action is not None:
        widget.clicked.connect(action)
    return widget


def row(*items, spacing=8):
    line = QtWidgets.QHBoxLayout()
    line.setSpacing(spacing)
    for item in items:
        if item is None:
            line.addStretch()
        elif isinstance(item, QtWidgets.QLayout):
            line.addLayout(item)
        else:
            line.addWidget(item)
    return line


def card(role="card", spacing=8):
    frame = QtWidgets.QFrame()
    frame.setProperty("role", role)
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(11, 10, 11, 10)
    layout.setSpacing(spacing)
    return frame, layout


def section(text):
    divider = QtWidgets.QFrame()
    divider.setProperty("role", "divider")
    divider.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    return row(label(text, role="section"), divider)


def scroll_page():
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setAttribute(Qt.WA_AlwaysShowToolTips)
    page = QtWidgets.QWidget()
    page.setProperty("page", True)
    layout = QtWidgets.QVBoxLayout(page)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(11)
    scroll.setWidget(page)
    return scroll, layout


class Segments(QtWidgets.QFrame):
    def __init__(self, choices, change, monospace=False):
        super().__init__()
        self.setProperty("role", "segments")
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self._group = QtWidgets.QButtonGroup(self)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        self.buttons = {}
        for value, name, text in choices:
            option = button(text, name, lambda checked=False, value=value: change(value), "segment")
            option.setCheckable(True)
            if monospace:
                numeric(option)
                option.setStyleSheet("padding-left: 6px; padding-right: 6px;")
            self._group.addButton(option)
            self.buttons[value] = option
            layout.addWidget(option)

    def select(self, value):
        if value in self.buttons:
            self.buttons[value].setChecked(True)
        elif self._group.checkedButton() is not None:
            self._group.setExclusive(False)
            self._group.checkedButton().setChecked(False)
            self._group.setExclusive(True)


class WrappingNote(QtWidgets.QTextBrowser):
    """Selectable note that grows with wrapped content inside the page scroll area."""

    def __init__(self):
        super().__init__()
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.document().setDocumentMargin(0)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)

    def _fit(self, size):
        height = max(self.fontMetrics().height(), int(size.height() + 1))
        if self.height() != height:
            self.setFixedHeight(height)


class LinkBeam(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(38)
        self._phase = 0
        self._state = "idle"
        self._driving = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(65)
        self._timer.timeout.connect(self._advance)

    @property
    def animation_active(self):
        return self._timer.isActive()

    def set_state(self, connection, driving):
        self._state = connection
        self._driving = driving
        animate = connection == "reconnecting" or (connection == "connected" and driving == "maya")
        if animate and qt_compat.animations_enabled() and self.isVisible():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def hideEvent(self, event):
        self._timer.stop()
        self._phase = 0
        super().hideEvent(event)

    def _advance(self):
        self._phase = (self._phase + (1 if self._state == "reconnecting" else 3)) % 14
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        y = self.height() // 2
        painter.setPen(QtGui.QColor(style.BORDER))
        painter.drawLine(0, y, self.width(), y)
        if self._state == "reconnecting" or (self._state == "connected" and self._driving == "maya"):
            color = style.AMBER if self._state == "reconnecting" else style.MINT
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QtGui.QColor(color))
            for x in range(self._phase if self.animation_active else 4, self.width(), 14):
                painter.drawEllipse(QtCore.QPointF(x, y), 1.6, 1.6)


class CameraStrip(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(22)
        self._segments = ()

    def set_segments(self, segments):
        self._segments = tuple((max(1, int(frames)), bool(updated)) for frames, updated in segments)
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        segments = self._segments
        if not segments:
            painter.setBrush(QtGui.QColor(style.HEADER))
            painter.drawRoundedRect(self.rect(), 4, 4)
            return
        width = max(0.0, (self.width() - 2 * (len(segments) - 1)) /
                    sum(frames for frames, _ in segments))
        x = 0.0
        for frames, updated in segments:
            span = frames * width
            painter.setBrush(QtGui.QColor(style.AMBER if updated else "#4A4F55"))
            painter.drawRoundedRect(QtCore.QRectF(x, 0, span, self.height()), 3, 3)
            x += span + 2


class PerformanceBar(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(6)
        self._fraction = 0

    def set_measurement(self, milliseconds, budget):
        self._fraction = max(0.0, milliseconds / budget) if milliseconds is not None and budget else 0
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QtGui.QColor(style.HEADER))
        painter.drawRoundedRect(self.rect(), 3, 3)
        if self._fraction:
            painter.setBrush(QtGui.QColor(style.AMBER if self._fraction > 1.0 else style.MINT))
            painter.drawRoundedRect(
                QtCore.QRectF(0, 0, max(4, self.width() * min(1.0, self._fraction)), self.height()), 3, 3)
