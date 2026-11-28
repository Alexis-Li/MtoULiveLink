# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Qt widget tests under Maya's Python, without the Standalone application."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


class QtWindowTests(unittest.TestCase):
    def test_qt_windows_wrap_and_select_diagnostics_and_keep_long_names(self):
        from mtou_livelink.controller import Controller
        from mtou_livelink.diagnostics import make_diagnostic
        from mtou_livelink.qt_compat import QtGui, QtWidgets
        from mtou_livelink.ui.panel import PanelWindow

        global _qt_application
        _qt_application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        controller = Controller()
        window = PanelWindow(controller, lambda _: None)
        self.addCleanup(window.close)
        window.show()
        tabs = window.findChild(QtWidgets.QTabWidget, "panelTabs")
        self.assertEqual(["连接", "预览", "相机", "诊断"],
                         [tabs.tabText(index) for index in range(tabs.count())])
        long_sequence = "LevelSequence_" * 12
        window.set_camera_display(sequence=long_sequence)
        tabs.setCurrentIndex(2)
        _qt_application.processEvents()
        sequence = window.findChild(QtWidgets.QLabel, "cameraSequence")
        self.assertIn("…", sequence.text())
        self.assertEqual(long_sequence, sequence.toolTip())

        long_error = "|root|" + "long_ancestor_without_spaces|" * 50
        controller._show_error(make_diagnostic("SKELETON_MISMATCH", details=long_error))
        tabs.setCurrentIndex(3)
        _qt_application.processEvents()
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
        details.resize(900, 500)
        _qt_application.processEvents()
        self.assertGreater(view.width(), 850)


if __name__ == "__main__":
    unittest.main()
