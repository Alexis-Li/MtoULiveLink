# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""Scene-keyed settings in Maya's user preferences."""

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .config import DEFAULT_PLAYBACK_CAP, WORKFLOW_ANIMATION, WORKFLOWS
from .timeline import normalize_playback_cap


@dataclass(frozen=True)
class SceneSettings:
    workflow: str = WORKFLOW_ANIMATION
    blendshapes_enabled: bool = True
    show_popups: bool = True
    playback_cap: int = DEFAULT_PLAYBACK_CAP
    custom_range: bool = False
    range_start: int = 1
    range_end: int = 24
    root: str = ""
    secondary_root: str = ""
    display: str = ""
    display_attribute: str = ""
    secondary_display: str = ""
    secondary_display_attribute: str = ""

    @classmethod
    def from_record(cls, record, defaults):
        if not isinstance(record, dict):
            raise ValueError("settings must be an object")
        values = asdict(defaults)
        for field in fields(cls):
            value = record.get(field.name)
            if type(value) is type(values[field.name]):
                values[field.name] = value
        if values["workflow"] not in WORKFLOWS:
            values["workflow"] = defaults.workflow
        values["playback_cap"] = normalize_playback_cap(values["playback_cap"])
        return cls(**values)


class ScenePreferences:
    FOLDER_NAME = "MtoULiveLinkScenes"
    VERSION = 1

    def __init__(self, user_preferences):
        self.folder = Path(user_preferences) / self.FOLDER_NAME

    @staticmethod
    def scene_key(path):
        return os.path.normcase(os.path.normpath(os.path.abspath(path))) if path else ""

    def path_for(self, scene_path):
        key = self.scene_key(scene_path)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.folder / ("{0}.json".format(digest))

    def load(self, scene_path, defaults):
        if not scene_path:
            return defaults
        try:
            with self.path_for(scene_path).open(encoding="utf-8") as source:
                document = json.load(source)
        except FileNotFoundError:
            return defaults
        if not isinstance(document, dict) or document.get("version") != self.VERSION:
            raise ValueError("unsupported settings file")
        return SceneSettings.from_record(document.get("settings"), defaults)

    def save(self, scene_path, settings):
        if not scene_path:
            return
        path = self.path_for(scene_path)
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.folder,
                                    delete=False) as target:
                temporary = Path(target.name)
                json.dump({"version": self.VERSION, "settings": asdict(settings)}, target,
                          ensure_ascii=False, indent=2)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
