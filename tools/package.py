"""Build deterministic Maya and Unreal component archives; dry-run unless --apply is used."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from _project_tools import (
    MAYA_COMPONENT,
    ROOT,
    UNREAL_COMPONENT,
    display_path,
    emit,
    maya_version,
    unreal_version,
)

PRODUCT = "MtoULiveLink"
MAYA_INCLUDED = ("scripts", "plug-ins", "icons", "presets", "install.mel")
LEGAL_FILES = ("LICENSE", "NOTICE")
UNREAL_EXCLUDED_DIRS = {"Binaries", "DerivedDataCache", "Intermediate", "Saved", ".vs", ".git"}


def _write_archive(archive: Path, entries: list[tuple[str, Path]]) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name, source in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, source.read_bytes())


def _legal_entries(prefix: str = "") -> list[tuple[str, Path]]:
    """The license files every archive must carry, from the repository root."""
    paths = [ROOT / name for name in LEGAL_FILES]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise ValueError("missing legal files: {0}".format(", ".join(missing)))
    return [(prefix + path.name, path) for path in paths]


def _result(archive: Path, entries: list, apply: bool) -> dict:
    return {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "archive": display_path(archive),
        "file_count": len(entries),
    }


def package_maya(output_dir: Path, apply: bool = False) -> dict:
    files = []
    for name in MAYA_INCLUDED:
        item = MAYA_COMPONENT / name
        if item.is_file():
            files.append(item)
        elif item.is_dir():
            files.extend(
                path for path in item.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            )
    entries = [(path.relative_to(MAYA_COMPONENT).as_posix(), path) for path in files]
    entries += _legal_entries()
    archive = output_dir / "{0}-{1}.zip".format(PRODUCT, maya_version())
    if apply:
        _write_archive(archive, entries)
    return _result(archive, entries, apply)


def package_unreal(engine: str, output_dir: Path, apply: bool = False) -> dict:
    files = [
        path for path in UNREAL_COMPONENT.rglob("*")
        if path.is_file()
        and not any(part in UNREAL_EXCLUDED_DIRS
                    for part in path.relative_to(UNREAL_COMPONENT).parts)
    ]
    entries = [
        ((Path(PRODUCT) / path.relative_to(UNREAL_COMPONENT)).as_posix(), path)
        for path in files
    ]
    entries += _legal_entries(PRODUCT + "/")
    archive = output_dir / "{0}-{1}-UE{2}.zip".format(PRODUCT, unreal_version(), engine)
    if apply:
        _write_archive(archive, entries)
    return _result(archive, entries, apply)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", choices=("all", "maya", "unreal"), nargs="?", default="all")
    parser.add_argument("--engine", default="5.7", help="Engine version used in the archive name.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "releases")
    parser.add_argument(
        "--apply", action="store_true", help="Write the archives; otherwise preview only."
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output_dir = output_dir.resolve()
    try:
        results = []
        if args.host in ("all", "maya"):
            results.append(package_maya(output_dir, args.apply))
        if args.host in ("all", "unreal"):
            results.append(package_unreal(args.engine, output_dir, args.apply))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        emit({"ok": False, "error": str(exc)}, args.json)
        return 2
    emit({
        "ok": True,
        "mode": results[0]["mode"],
        "archives": [result["archive"] for result in results],
        "file_count": sum(result["file_count"] for result in results),
    }, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
