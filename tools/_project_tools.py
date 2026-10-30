"""Shared standard-library helpers for MtoULiveLink project commands."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAYA_COMPONENT = ROOT / "maya" / "MtoULiveLink"
UNREAL_COMPONENT = ROOT / "unreal" / "MtoULiveLink"
UPLUGIN = UNREAL_COMPONENT / "MtoULiveLink.uplugin"
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
MAYA_VERSION_RE = re.compile(r'((?:__version__|PLUGIN_VERSION)\s*=\s*["\'])' r'([^"\']+)(["\'])')


def emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for key, value in payload.items():
        if isinstance(value, list):
            print("{0}:".format(key))
            for item in value:
                print("  - {0}".format(item))
        else:
            print("{0}: {1}".format(key, value))


def require_semver(value: str) -> str:
    if not SEMVER_RE.fullmatch(value):
        raise ValueError("version must be Semantic Versioning, for example 1.2.3")
    return value


def maya_runtime_files(component: Path = MAYA_COMPONENT) -> list[Path]:
    files = []
    for directory in (component / "scripts", component / "plug-ins"):
        if directory.is_dir():
            files.extend(directory.rglob("*.py"))
    return sorted(files)


def maya_version(component: Path = MAYA_COMPONENT) -> str:
    versions = {
        match.group(2)
        for path in maya_runtime_files(component)
        for match in MAYA_VERSION_RE.finditer(path.read_text(encoding="utf-8-sig"))
    }
    if len(versions) != 1:
        raise ValueError("Maya component must declare one consistent runtime version")
    return require_semver(versions.pop())


def unreal_metadata(descriptor: Path = UPLUGIN) -> dict:
    return json.loads(descriptor.read_text(encoding="utf-8-sig"))


def unreal_version(descriptor: Path = UPLUGIN) -> str:
    return require_semver(str(unreal_metadata(descriptor).get("VersionName") or ""))


def display_path(path: Path) -> str:
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
