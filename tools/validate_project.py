"""Validate project layout, versions, protocol corpus, history archives and forbidden files."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from _project_tools import ROOT, emit, maya_version, unreal_version

REQUIRED_PATHS = (
    "AGENTS.md",
    "CONTEXT.md",
    "README.md",
    "README_CN.md",
    "CHANGELOG.md",
    "LICENSE",
    ".gitignore",
    ".gitattributes",
    "docs/workflow.md",
    "docs/history/README.md",
    "maya/MtoULiveLink/install.mel",
    "maya/MtoULiveLink/scripts/MtoULiveLink.py",
    "maya/MtoULiveLink/tests",
    "unreal/MtoULiveLink/MtoULiveLink.uplugin",
    "unreal/MtoULiveLink/Source",
    "protocol/generate_unreal_corpus.py",
    "tools/issue_report.py",
)
FORBIDDEN_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "binaries",
    "deriveddatacache",
    "intermediate",
    "saved",
    ".vs",
}
FORBIDDEN_SUFFIXES = (
    ".pyc", ".pyo", ".sln", ".suo", ".opensdf", ".sdf", ".vc.db", ".vc.opendb", ".zip", ".7z",
)
TEXT_SUFFIXES = {
    ".py", ".md", ".json", ".ini", ".uplugin", ".uproject", ".cs", ".cpp", ".h", ".inl",
    ".toml", ".yml", ".yaml", ".mel", ".ps1", ".cmd",
}
JSON_SUFFIXES = {".json", ".uplugin"}
ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]|(?<![A-Za-z0-9_.-])/(?:Users|home|private/tmp|var/folders)/"
)
ARCHIVE_SUFFIX = "comment-archive.json"


def _version_control_paths(root: Path) -> list[Path]:
    """Return tracked and unignored paths, excluding ignored local outputs."""
    command = [
        "git", "-c", "safe.directory={0}".format(root.as_posix()), "-C", str(root),
        "ls-files", "--cached", "--others", "--exclude-standard", "-z",
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    return [
        root / value.decode("utf-8", errors="surrogateescape")
        for value in completed.stdout.split(b"\0")
        if value
    ]


def _issue_archive_errors(archive: object) -> list[str]:
    """Validate the preserved comment archives, not current issue status."""
    if not isinstance(archive, dict):
        return ["archive must be an object"]
    errors = []
    if archive.get("schema") != "github-issue-comment-archive/1":
        errors.append("unsupported archive schema")
    issue = archive.get("issue", "")
    parsed = urlparse(issue if isinstance(issue, str) else "")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or not re.fullmatch(r"/[^/]+/[^/]+/issues/[1-9][0-9]*", parsed.path)
        or parsed.query
        or parsed.fragment
    ):
        errors.append("issue must be a canonical GitHub issue URL")
    for field in ("archived_on", "purpose", "current_status_comment"):
        if not isinstance(archive.get(field), str) or not archive[field].strip():
            errors.append(f"missing archive {field}")
    try:
        date.fromisoformat(archive.get("archived_on", ""))
    except (TypeError, ValueError):
        errors.append("archived_on must be an ISO date")
    current = archive.get("current_status_comment", "")
    if not isinstance(current, str) or not re.fullmatch(
        re.escape(str(issue)) + r"#issuecomment-[1-9][0-9]*", current
    ):
        errors.append("current_status_comment must refer to this issue")
    comments = archive.get("comments")
    if not isinstance(comments, list) or not comments:
        return errors + ["archive must preserve at least one source comment"]
    seen = set()
    for index, comment in enumerate(comments):
        prefix = f"comment {index + 1}: "
        if not isinstance(comment, dict):
            errors.append(prefix + "must be an object")
            continue
        comment_id = comment.get("id")
        if type(comment_id) is not int or comment_id <= 0:
            errors.append(prefix + "id must be a positive integer")
        elif comment_id in seen:
            errors.append(prefix + "duplicate id")
        else:
            seen.add(comment_id)
        for field in ("author", "body", "created_at", "updated_at", "original_url"):
            if not isinstance(comment.get(field), str) or not comment[field].strip():
                errors.append(prefix + f"missing {field}")
        for field in ("created_at", "updated_at"):
            try:
                timestamp = datetime.fromisoformat(comment.get(field, "").replace("Z", "+00:00"))
                if timestamp.utcoffset() is None:
                    raise ValueError
            except (AttributeError, TypeError, ValueError):
                errors.append(prefix + f"{field} must include a timezone")
        if comment.get("original_url") != f"{issue}#issuecomment-{comment_id}":
            errors.append(prefix + "original_url does not match issue and comment id")
    metadata = dict(archive)
    metadata["comments"] = [
        {key: value for key, value in comment.items() if key != "body"}
        if isinstance(comment, dict) else comment
        for comment in comments
    ]
    if ABSOLUTE_PATH_RE.search(json.dumps(metadata, ensure_ascii=False)):
        errors.append("archive metadata must not contain machine-specific absolute paths")
    return errors


def _path_policy_text(relative_path: str, text: str) -> str:
    """Exclude only preserved source bodies in structurally valid issue archives."""
    if relative_path.startswith("docs/history/") and relative_path.endswith(ARCHIVE_SUFFIX):
        try:
            archive = json.loads(text)
        except ValueError:
            return text
        if not _issue_archive_errors(archive):
            # Historical comments must remain exact, including old machine paths.
            for comment in archive["comments"]:
                comment.pop("body")
            return json.dumps(archive, ensure_ascii=False)
    return text


def _iter_files(root: Path):
    for current, directories, files in os.walk(root):
        directories[:] = [name for name in directories
                          if name not in {".git", ".tmp", ".worktrees"}
                          and name.lower() not in FORBIDDEN_DIRS]
        for name in files:
            yield Path(current) / name


def _validate_files(root: Path, errors: list[str]) -> None:
    for path in _version_control_paths(root):
        relative_path = path.relative_to(root).as_posix()
        if any(part.lower() in FORBIDDEN_DIRS for part in path.relative_to(root).parts):
            errors.append("version-control-visible generated path: {0}".format(relative_path))
        if path.name.lower().endswith(FORBIDDEN_SUFFIXES):
            errors.append("version-control-visible generated file: {0}".format(relative_path))
    for path in _iter_files(root):
        relative_path = path.relative_to(root).as_posix()
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            errors.append("text file is not UTF-8: {0}".format(relative_path))
            continue
        if ABSOLUTE_PATH_RE.search(_path_policy_text(relative_path, text)):
            errors.append("machine-specific absolute path in {0}".format(relative_path))
        if path.suffix.lower() in JSON_SUFFIXES:
            try:
                document = json.loads(text)
            except ValueError as exc:
                errors.append("invalid JSON {0}: {1}".format(relative_path, exc))
                continue
            if relative_path.startswith("docs/history/") and path.name.endswith(ARCHIVE_SUFFIX):
                errors.extend(
                    f"{relative_path}: {problem}" for problem in _issue_archive_errors(document)
                )


def _validate_versions(errors: list[str]) -> None:
    try:
        maya, unreal = maya_version(), unreal_version()
    except (OSError, ValueError) as exc:
        errors.append("invalid product version: {0}".format(exc))
        return
    if maya != unreal:
        errors.append(
            "Maya version {0} differs from Unreal VersionName {1}".format(maya, unreal)
        )


def _validate_conformance(root: Path, errors: list[str]) -> None:
    generator_path = root / "protocol" / "generate_unreal_corpus.py"
    spec = importlib.util.spec_from_file_location("generate_mtou_unreal_corpus", generator_path)
    if spec is None or spec.loader is None:
        errors.append("cannot load MtoU conformance generator")
        return
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        errors.extend(module.check_generated())
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append("invalid MtoU conformance corpus: {0}".format(exc))


def validate(root: Path = ROOT) -> dict:
    root = root.resolve()
    errors: list[str] = []
    for relative_path in REQUIRED_PATHS:
        if not (root / relative_path).exists():
            errors.append("missing required path: {0}".format(relative_path))
    _validate_files(root, errors)
    _validate_versions(errors)
    _validate_conformance(root, errors)
    return {"ok": not errors, "errors": sorted(set(errors))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    args = parser.parse_args(argv)
    result = validate()
    emit(result, args.json)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
