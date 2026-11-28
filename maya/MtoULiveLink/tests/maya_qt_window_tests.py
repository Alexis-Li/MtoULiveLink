# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Qt widget tests under Maya's Python, without the Standalone application."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


class QtWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mtou_livelink.qt_compat import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from mtou_livelink.controller import Controller
        from mtou_livelink.ui.panel import PanelWindow

        self.controller = Controller()
        self.window = PanelWindow(self.controller, lambda _: None)
        self.addCleanup(self.window.close)

    def test_qt_windows_wrap_and_select_diagnostics_and_keep_long_names(self):
        from mtou_livelink.diagnostics import make_diagnostic
        from mtou_livelink.qt_compat import QtGui, QtWidgets
        from mtou_livelink.strings import text

        window = self.window
        window.show()
        tabs = window.findChild(QtWidgets.QTabWidget, "panelTabs")
        self.assertEqual(["连接", "预览", "相机", "诊断"],
                         [tabs.tabText(index) for index in range(tabs.count())])
        long_sequence = "📷" + "LevelSequence_" * 12
        window.set_camera_display(sequence=long_sequence)
        tabs.setCurrentIndex(2)
        self.app.processEvents()
        sequence = window.findChild(QtWidgets.QLabel, "cameraSequence")
        self.assertIn("…", sequence.text())
        self.assertEqual(long_sequence, sequence.toolTip())

        long_error = "|root|" + "long_ancestor_without_spaces|" * 50
        self.controller._show_error(make_diagnostic("SKELETON_MISMATCH", details=long_error))
        tabs.setCurrentIndex(3)
        self.app.processEvents()
        note = window.findChild(QtWidgets.QTextBrowser, "diagnosticLatest")
        self.assertIn(long_error, note.toPlainText())
        self.assertEqual(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere, note.wordWrapMode())
        self.assertGreater(tabs.currentWidget().verticalScrollBar().maximum(), 0)
        details = window.show_diagnostics()
        view = details.findChild(QtWidgets.QTextBrowser, "diagnosticDetails")
        self.assertIn(long_error, view.toPlainText())
        cursor = view.document().find(long_error)
        self.assertEqual(long_error, cursor.selectedText())
        view.setTextCursor(cursor)
        self.assertEqual(long_error, view.textCursor().selectedText())

        # The new error is shorter than the old selection's starting offset.
        # Keep the selected text across paragraphs and its backward direction.
        first = view.document().find(long_sequence).selectionStart()
        last = view.document().find(text("no_connection_difference")).selectionEnd()
        cursor.setPosition(last)
        cursor.setPosition(first, QtGui.QTextCursor.KeepAnchor)
        view.setTextCursor(cursor)
        selected = cursor.selectedText()
        self.assertIn("\u2029", selected)
        self.controller._show_error(make_diagnostic("MODE_CONFLICT"))
        self.assertGreater(first, view.document().characterCount())
        self.assertEqual(selected, view.textCursor().selectedText())
        self.assertGreater(view.textCursor().anchor(), view.textCursor().position())

        details.resize(900, 500)
        self.app.processEvents()
        self.assertGreater(view.width(), 850)

    def test_repeated_runtime_error_updates_both_diagnostic_views(self):
        from mtou_livelink.diagnostics import make_diagnostic
        from mtou_livelink.qt_compat import QtWidgets

        details = self.window.show_diagnostics()
        complete = details.findChild(QtWidgets.QTextBrowser, "diagnosticDetails")
        recent = self.window.findChild(QtWidgets.QTextBrowser, "diagnosticLatest")
        self.controller._session = session = SimpleNamespace(is_ready=True)
        self.addCleanup(setattr, self.controller, "_session", None)
        self.controller._interact = popup = mock.Mock()
        for code, error_details in (("INVALID_BINDING", "target A"), ("MODE_CONFLICT", "mode B"),
                                    ("INVALID_BINDING", "target A")):
            self.controller._show_runtime_error(
                make_diagnostic(code, details=error_details), session, "preview",
                self.controller._diagnostics.stamp)
            self.assertIn(error_details, recent.toPlainText())
            self.assertIn(error_details, complete.toPlainText())
        self.assertEqual(2, popup.call_count)

    def test_outfit_attribute_picker_uses_segments_for_two_through_five_options(self):
        from mtou_livelink import qt_compat
        from mtou_livelink.qt_compat import QtCore, QtWidgets
        from mtou_livelink.strings import text

        for count, accept in ((2, True), (5, True), (5, False), (6, True), (6, False)):
            with self.subTest(count=count, accept=accept):
                candidates = tuple("wardrobe{0}".format(index) for index in range(count))

                def choose(dialog):
                    dialog.show()
                    self.app.processEvents()
                    try:
                        combo = dialog.findChild(QtWidgets.QComboBox)
                        if count <= 5:
                            self.assertIsNone(combo)
                            options = [item for item in dialog.findChildren(QtWidgets.QPushButton)
                                       if item.property("role") == "segment"]
                            self.assertEqual(candidates, tuple(item.text() for item in options))
                            points = [item.mapTo(dialog, QtCore.QPoint(0, 0)) for item in options]
                            self.assertEqual(1, len({point.y() for point in points}))
                            self.assertEqual(sorted(point.x() for point in points),
                                             [point.x() for point in points])
                            options[-1].click()
                            self.assertTrue(options[-1].isChecked())
                        else:
                            self.assertIsNotNone(combo)
                            self.assertEqual(candidates, tuple(combo.itemText(i) for i in range(combo.count())))
                            combo.setCurrentIndex(count - 1)
                        control = text("ok") if accept else text("cancel")
                        next(item for item in dialog.findChildren(QtWidgets.QPushButton)
                             if item.text() == control).click()
                        return dialog.result()
                    finally:
                        dialog.close()

                with mock.patch.object(qt_compat, "dialog_exec", side_effect=choose):
                    result = self.window._interact("choose_enum", candidates)
                self.assertEqual(candidates[-1] if accept else None, result)


if __name__ == "__main__":
    unittest.main()
