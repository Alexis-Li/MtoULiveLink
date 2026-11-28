# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""MtoU Live Link Maya entry point, loaded by the shelf or Script Editor."""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from mtou_livelink.cache import PlaybackCache
    from mtou_livelink.config import PROTOCOL_VERSION, __version__
    from mtou_livelink.controller import Controller
    from mtou_livelink.scene import require_maya
except ImportError as _error:
    _missing = getattr(_error, "name", None) or ""
    if not _missing.startswith("mtou_livelink"):
        raise
    raise ImportError(
        "MtoU_LiveLink 安装不完整：找不到 {0}。请重新完整解压 Maya 组件，把其中的 "
        "install.mel 拖入 Maya 视口重新安装，然后重启 Maya。".format(_missing),
        name=_missing) from _error


_window = None


def _closed(window):
    global _window
    if _window is window:
        _window = None


def run():
    global _window
    require_maya()
    from mtou_livelink.qt_compat import maya_main_window
    from mtou_livelink.ui.panel import PanelWindow

    if _window is None:
        try:
            PlaybackCache.cleanup_stale()
        except (OSError, TypeError, ValueError):
            pass
        _window = PanelWindow(Controller(), _closed, maya_main_window())
        try:
            _window.start()
        except Exception:
            _window.close()
            raise
    if _window.isMinimized():
        _window.showNormal()
    else:
        _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


def main():
    return run()


if __name__ == "__main__":
    main()
