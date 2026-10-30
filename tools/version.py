"""Set the product version of both components; dry-run unless --apply is used."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from _project_tools import (
    MAYA_VERSION_RE,
    UPLUGIN,
    display_path,
    emit,
    maya_runtime_files,
    require_semver,
)

VERSION_NAME_RE = re.compile(r'("VersionName"\s*:\s*)"(?:[^"\\]|\\.)*"')
VERSION_NUMBER_RE = re.compile(r'("Version"\s*:\s*)-?\d+')


def _changes(version: str) -> list[tuple[Path, str]]:
    changes = []
    for runtime_file in maya_runtime_files():
        text = runtime_file.read_text(encoding="utf-8-sig")
        replaced, count = MAYA_VERSION_RE.subn(
            lambda match: match.group(1) + version + match.group(3), text
        )
        if count:
            changes.append((runtime_file, replaced))
    if not changes:
        raise ValueError("Maya component must declare __version__ or PLUGIN_VERSION")
    changes.append((UPLUGIN, uplugin_text(UPLUGIN.read_text(encoding="utf-8-sig"), version)))
    return changes


def uplugin_text(text: str, version: str) -> str:
    """Set VersionName and increment Version in place, keeping the descriptor's layout."""
    number = json.loads(text).get("Version")
    if isinstance(number, bool) or not isinstance(number, int):
        raise ValueError(".uplugin Version must be an integer")
    replaced, names = VERSION_NAME_RE.subn(
        lambda match: match.group(1) + json.dumps(version), text, count=1)
    replaced, numbers = VERSION_NUMBER_RE.subn(
        lambda match: match.group(1) + str(number + 1), replaced, count=1)
    updated = json.loads(replaced)
    if (names, numbers) != (1, 1) or (
            updated.get("VersionName"), updated.get("Version")) != (version, number + 1):
        raise ValueError(".uplugin must declare top-level VersionName and Version")
    return replaced


def update(version: str, apply: bool = False) -> dict:
    changes = _changes(require_semver(version))
    if apply:
        for path, text in changes:
            path.write_text(text, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "version": version,
        "files": [display_path(path) for path, _ in changes],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Semantic version, for example 1.2.3.")
    parser.add_argument(
        "--apply", action="store_true", help="Write changes; otherwise preview only."
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = update(args.version, args.apply)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        emit({"ok": False, "error": str(exc)}, args.json)
        return 2
    emit(result, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
