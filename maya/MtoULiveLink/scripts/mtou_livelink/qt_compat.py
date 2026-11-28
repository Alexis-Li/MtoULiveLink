# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Qt bindings used by the Maya panel, loaded only when the window opens."""

import ctypes
import sys

from .mayaapi import omui

try:
    from PySide2 import QtCore, QtGui, QtWidgets
    from shiboken2 import isValid, wrapInstance
except ImportError:
    from PySide6 import QtCore, QtGui, QtWidgets
    from shiboken6 import isValid, wrapInstance


def maya_main_window():
    pointer = omui.MQtUtil.mainWindow() if omui is not None else None
    return wrapInstance(int(pointer), QtWidgets.QWidget) if pointer else None


def dialog_exec(dialog):
    execute = getattr(dialog, "exec", None) or dialog.exec_
    return execute()


def animations_enabled():
    if sys.platform != "win32":
        return True
    enabled = ctypes.c_int()
    # SPI_GETCLIENTAREAANIMATION, an accessibility preference.
    if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
        return bool(enabled.value)
    return True
