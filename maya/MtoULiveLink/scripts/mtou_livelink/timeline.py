# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Maya timeline, playback range and playback cap helpers."""

from .config import DEFAULT_PLAYBACK_CAP, PLAYBACK_CAP_CHOICES, PLAYBACK_CAP_OPTION_VAR
from .mayaapi import cmds
from .scene import require_maya


def normalize_playback_cap(value):
    text = str(value).strip().lower()
    if text in ("follow scene", "follow_scene", "scene"):
        return 60
    for choice in PLAYBACK_CAP_CHOICES:
        if text in (str(choice), "{0} fps".format(choice)):
            return choice
    return DEFAULT_PLAYBACK_CAP


def load_playback_cap():
    try:
        if cmds is not None and cmds.optionVar(exists=PLAYBACK_CAP_OPTION_VAR):
            return normalize_playback_cap(cmds.optionVar(query=PLAYBACK_CAP_OPTION_VAR))
    except (AttributeError, RuntimeError, TypeError):
        pass
    return DEFAULT_PLAYBACK_CAP


def maya_is_playing():
    if cmds is None:
        return False
    try:
        return bool(cmds.play(query=True, state=True))
    except (AttributeError, RuntimeError, TypeError):
        return False


class MayaTimeline:
    """Small adapter around Maya's current frame and Playback Range APIs."""

    def current_frame(self):
        require_maya()
        return cmds.currentTime(query=True)

    def playback_range(self):
        require_maya()
        return (
            cmds.playbackOptions(query=True, min=True),
            cmds.playbackOptions(query=True, max=True),
        )

    def is_playing(self):
        return maya_is_playing()

    def stop(self):
        if self.is_playing():
            cmds.play(state=False)

    def set_frame(self, frame):
        require_maya()
        cmds.currentTime(frame, edit=True)
