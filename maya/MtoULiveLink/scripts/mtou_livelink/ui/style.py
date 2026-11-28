# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Theme for the two native Maya windows."""

BACKGROUND = "#26282B"
HEADER = "#1C1E20"
CARD = "#2F3236"
BORDER = "#3B3F44"
TEXT = "#E6E8EA"
DIM = "#9AA1A9"
MINT = "#3DDC97"
AMBER = "#F2B84B"
CORAL = "#FF8A7E"

STYLESHEET = """
QDialog, QTabWidget::pane, QScrollArea, QWidget[page="true"] {
    background: #26282B; color: #E6E8EA; border: none;
}
QLabel, QCheckBox { color: #E6E8EA; background: transparent; }
QLabel[tone="dim"], QCheckBox:disabled { color: #9AA1A9; }
QLabel[tone="mint"] { color: #3DDC97; }
QLabel[tone="amber"] { color: #F2B84B; }
QLabel[tone="coral"] { color: #FF8A7E; }
QLabel[role="section"] { color: #9AA1A9; font-size: 11px; }
QLabel[role="wordmark"] { color: #9AA1A9; font-size: 11px; font-weight: bold; }
QLabel[role="large"] { font-size: 16px; font-weight: bold; }
QLabel[role="chip"] {
    border: 1px solid #3B3F44; border-radius: 8px; padding: 2px 7px;
    color: #C9CED3; font-size: 11px;
}
QLabel[role="chip"][tone="mint"] { color: #3DDC97; border-color: #3DDC97; }
QLabel[role="chip"][tone="amber"] { color: #F2B84B; border-color: #F2B84B; }
QScrollBar:vertical { width: 10px; background: #26282B; margin: 0; border: 0; }
QScrollBar::handle:vertical { background: #51565C; min-height: 22px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QFrame[role="header"] { background: #1C1E20; border-bottom: 1px solid #3B3F44; }
QFrame[role="card"] { background: #2F3236; border: 1px solid #3B3F44; border-radius: 10px; }
QFrame[role="empty"] { border: 1px dashed #4A4F55; border-radius: 10px; }
QFrame[role="stat"] { background: #26282B; border-radius: 8px; }
QFrame[role="segments"] { background: #1C1E20; border: 1px solid #3B3F44; border-radius: 8px; }
QFrame[role="divider"] { background: #33373B; max-height: 1px; min-height: 1px; }
QFrame[severity="failure"] { background: #3A2A2A; border-color: #7A3F3A; }
QFrame[severity="warning"] { background: #3B3228; border-color: #745834; }
QPushButton {
    background: #2F3236; color: #E6E8EA; border: 1px solid #4A4F55;
    border-radius: 8px; padding: 4px 12px; min-height: 20px;
}
QPushButton[role="ghost"] { background: transparent; }
QPushButton:hover { border-color: #9AA1A9; }
QPushButton:focus, QCheckBox:focus, QSpinBox:focus { border-color: #3DDC97; }
QPushButton:pressed { background: #3B3F44; }
QPushButton[role="primary"] {
    background: #3DDC97; border-color: #3DDC97; color: #10241B; font-weight: bold;
}
QPushButton[role="primary"]:hover { background: #77E5B3; }
QPushButton[role="segment"] {
    background: transparent; border: 1px solid transparent; border-radius: 5px;
    color: #9AA1A9; padding: 1px 10px; min-height: 20px;
}
QPushButton[role="segment"]:checked { background: #3B3F44; color: #E6E8EA; }
QPushButton[role="segment"]:hover, QPushButton[role="segment"]:focus { border-color: #6A737C; }
QPushButton:disabled, QPushButton[role="primary"]:disabled, QPushButton[role="segment"]:disabled {
    background: #292C2F; border-color: #3B3F44; color: #888F96;
}
QTabBar::tab {
    color: #9AA1A9; background: #26282B; border-bottom: 2px solid #3B3F44;
    min-width: 90px; padding: 7px 4px 7px 4px;
}
QTabBar::tab:hover { color: #E6E8EA; background: #2F3236; }
QTabBar::tab:selected { color: #E6E8EA; border-bottom-color: #3DDC97; }
QSpinBox {
    color: #E6E8EA; background: #1C1E20; border: 1px solid #3B3F44;
    border-radius: 6px; padding: 3px 6px; selection-background-color: #3B3F44;
}
QSpinBox:disabled { color: #9AA1A9; }
QProgressBar {
    background: #1C1E20; border: 0; border-radius: 3px; min-height: 6px; max-height: 6px;
}
QProgressBar::chunk { background: #3DDC97; border-radius: 3px; }
QTextBrowser {
    background: #26282B; color: #E6E8EA; border: none;
    selection-background-color: #3B6157; selection-color: #FFFFFF;
}
"""
