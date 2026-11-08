"""Safely install or remove the approval dashboard in one Hermes profile.

The host's Python package must already be installed in its dashboard runtime.
This module manages only the static/plugin adapter payload under the selected
profile's ``plugins/approvals/dashboard`` directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

PLUGIN_NAME = "approvals"
PAYLOAD_NAME = "dashboard"
OWNER_FILE = ".hermes-approval-dashboard-install.json"
OWNER_FORMAT = 1


class InstallerError(RuntimeError):
    """Raised when the selected profile cannot be changed safely."""


def _profile_home(hermes_home: Path, profile: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", profile):
        raise InstallerError("profile must be a simple profile name")
    root = hermes_home.expanduser().resolve()
    candidate = root if profile == "default" else root / "profiles" / profile
    if not candidate.is_dir():
        raise InstallerError(f"Hermes profile does not exist: {profile}")
    resolved = candidate.resolve()
    if profile != "default":
        profiles_home = root / "profiles"
        resolved_profiles = profiles_home.resolve()
        if resolved_profiles.parent != root or resolved.parent != resolved_profiles:
            raise InstallerError(f"profile path escapes Hermes home: {profile}")
    return resolved


def _payload_files(source: Path) -> dict[str, Path]:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise InstallerError(f"dashboard payload source is not a directory: {source}")
    files: dict[str, Path] = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise InstallerError(f"dashboard payload must not contain symlinks: {path}")
        if path.is_file():
            rel = path.relative_to(source).as_posix()
            if not _safe_relpath(rel) or rel == OWNER_FILE:
                raise InstallerError(f"unsafe dashboard payload path: {rel}")
            files[rel] = path
        elif not path.is_dir():
            raise InstallerError(f"dashboard payload contains a non-regular entry: {path}")
    if not {"manifest.json", "plugin_api.py", "dist/index.js"} <= files.keys():
        raise InstallerError("dashboard payload is incomplete (manifest.json, plugin_api.py, dist/index.js required)")
    return files


def _safe_relpath(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and all(part not in ("", ".", "..") for part in path.parts)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_owner(marker: Path) -> tuple[dict[str, str], set[str]] | None:
    if not marker.exists():
        return None
    if marker.is_symlink() or not marker.is_file():
        raise InstallerError(f"installer ownership marker is not a regular file: {marker}")
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallerError(f"cannot read installer ownership marker {marker}: {exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("format") != OWNER_FORMAT
        or data.get("package") != "hermes-approval-api-dashboard"
        or not isinstance(data.get("files"), dict)
    ):
        raise InstallerError(f"unrecognized installer ownership marker: {marker}")
    files = data["files"]
    if any(not isinstance(name, str) or not _safe_relpath(name) or name == OWNER_FILE
           or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
           for name, digest in files.items()):
        raise InstallerError(f"invalid file ownership data in {marker}")
    directories = data.get("directories", [])
    if (not isinstance(directories, list)
            or any(not isinstance(name, str) or (name != "." and not _safe_relpath(name))
                   for name in directories)):
        raise InstallerError(f"invalid directory ownership data in {marker}")
    return files, set(directories)


def _atomic_write(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise InstallerError(f"refusing to replace symlink: {target}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(source.read_bytes())
        os.chmod(temp, source.stat().st_mode & 0o777)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_json(data: dict[str, Any], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temp, 0o600)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _target_file(target: Path, relative: str) -> Path:
    """Resolve one package-relative destination without following symlink parents."""
    path = target.joinpath(*PurePosixPath(relative).parts)
    parent = path.parent
    while parent != target.parent and parent != target:
        if parent.is_symlink():
            raise InstallerError(f"refusing symlinked package directory: {parent}")
        if parent.exists() and not parent.is_dir():
            raise InstallerError(f"package path parent is not a directory: {parent}")
        parent = parent.parent
    if path.is_symlink():
        raise InstallerError(f"refusing symlinked package file: {path}")
    return path


def _target_directory(target: Path, relative: str) -> Path:
    """Resolve an owned directory without traversing symlinked components."""
    path = target if relative == "." else target.joinpath(*PurePosixPath(relative).parts)
    cursor = target
    if cursor.is_symlink():
        raise InstallerError(f"refusing symlinked package directory: {cursor}")
    if relative != ".":
        for part in PurePosixPath(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise InstallerError(f"refusing symlinked package directory: {cursor}")
            if cursor.exists() and not cursor.is_dir():
                raise InstallerError(f"package path is not a directory: {cursor}")
    return path


def _target(profile_home: Path) -> Path:
    target = profile_home / "plugins" / PLUGIN_NAME / PAYLOAD_NAME
    # Resolve existing parents and ensure no symlink redirects writes outside
    # the selected profile. Missing final directories are handled on apply.
    cursor = target
    while not cursor.exists() and cursor != profile_home:
        cursor = cursor.parent
    if cursor.is_symlink():
        raise InstallerError(f"refusing symlinked plugin path: {cursor}")
    if cursor.exists() and not cursor.is_dir():
        raise InstallerError(f"plugin target path is not a directory: {cursor}")
    if cursor.resolve() != profile_home and profile_home not in cursor.resolve().parents:
        raise InstallerError("plugin target escapes the selected profile")
    return target


def install(hermes_home: Path, profile: str, dashboard_source: Path, *, dry_run: bool = False) -> str:
    profile_home = _profile_home(hermes_home, profile)
    sources = _payload_files(dashboard_source)
    target = _target(profile_home)
    marker = target / OWNER_FILE
    for name in sources:
        _target_file(target, name)
    owner_record = _load_owner(marker)
    owned = owner_record[0] if owner_record is not None else None
    owned_dirs = owner_record[1] if owner_record is not None else set()
    source_hashes = {name: _digest(path) for name, path in sources.items()}

    created_dirs: set[str] = set()
    if not target.exists():
        created_dirs.add(".")
    for name in sources:
        parent = PurePosixPath(name).parent
        while parent != PurePosixPath("."):
            directory = target.joinpath(*parent.parts)
            if not directory.exists():
                created_dirs.add(parent.as_posix())
            parent = parent.parent
    owned_dirs.update(created_dirs)

    if owned is None and target.exists():
        existing = [path for path in target.rglob("*") if path.is_file() or path.is_symlink()]
        if existing:
            raise InstallerError(f"refusing to adopt an existing unowned dashboard directory: {target}")
    if owned is not None:
        for name, old_hash in owned.items():
            existing_path = _target_file(target, name)
            if existing_path.exists() and (not existing_path.is_file() or _digest(existing_path) != old_hash):
                raise InstallerError(f"refusing to overwrite locally changed owned file: {existing_path}")
        for name in sources:
            path = _target_file(target, name)
            if name not in owned and path.exists():
                raise InstallerError(f"refusing to overwrite unowned file: {path}")

    changes = [name for name, digest in source_hashes.items()
               if owned is None or owned.get(name) != digest or not (target / name).is_file()]
    stale = sorted(set(owned or {}) - set(sources))
    if dry_run:
        return (f"Would install/update {PLUGIN_NAME} dashboard in profile {profile}; "
                f"write={len(changes)} files, remove={len(stale)} owned files; target={target}")

    target.mkdir(parents=True, exist_ok=True)
    # Remove stale package files only when their bytes still match our receipt.
    for name in stale:
        path = _target_file(target, name)
        if path.exists() and path.is_file() and _digest(path) == (owned or {}).get(name):
            path.unlink()
    for name, source in sources.items():
        path = _target_file(target, name)
        if owned is not None and owned.get(name) == source_hashes[name] and path.is_file():
            continue
        if path.exists() and name not in (owned or {}):
            raise InstallerError(f"refusing to overwrite unowned file: {path}")
        _atomic_write(source, path)
    _atomic_json({
        "format": OWNER_FORMAT,
        "package": "hermes-approval-api-dashboard",
        "files": source_hashes,
        "directories": sorted(owned_dirs),
    }, marker)
    return f"Installed dashboard payload in profile {profile}: {target}"


def uninstall(hermes_home: Path, profile: str, *, dry_run: bool = False) -> str:
    profile_home = _profile_home(hermes_home, profile)
    target = _target(profile_home)
    marker = target / OWNER_FILE
    owner_record = _load_owner(marker)
    if owner_record is None:
        return f"No installer-owned dashboard payload found in profile {profile}"
    owned, owned_dirs = owner_record

    removable: list[str] = []
    changed: list[str] = []
    for name, expected in owned.items():
        path = _target_file(target, name)
        if not path.exists():
            continue
        if not path.is_file() or _digest(path) != expected:
            changed.append(name)
        else:
            removable.append(name)
    for name in owned_dirs:
        _target_directory(target, name)
    if dry_run:
        return (f"Would uninstall dashboard from profile {profile}; remove={len(removable)} owned files, "
                f"preserve={len(changed)} changed files; target={target}")

    for name in removable:
        _target_file(target, name).unlink()
    marker.unlink()
    for name in sorted(owned_dirs, key=lambda item: len(PurePosixPath(item).parts), reverse=True):
        directory = _target_directory(target, name)
        try:
            directory.rmdir()
        except OSError:
            pass
    result = f"Removed only installer-owned dashboard files from profile {profile}"
    if changed:
        result += f"; preserved {len(changed)} locally changed file(s)"
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("install", "uninstall"):
        command = sub.add_parser(action)
        command.add_argument("--profile", required=True, help="explicit Hermes profile (use 'default' for the default profile)")
        command.add_argument("--hermes-home", type=Path, required=True, help="Hermes home/root containing the selected profile")
        if action == "install":
            command.add_argument("--dashboard-source", type=Path, required=True, help="dashboard payload directory from this package")
        command.add_argument("--dry-run", action="store_true", help="preview changes without writing anything")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "install":
            result = install(args.hermes_home, args.profile, args.dashboard_source, dry_run=args.dry_run)
        else:
            result = uninstall(args.hermes_home, args.profile, dry_run=args.dry_run)
    except InstallerError as exc:
        print(f"Dashboard installer failed: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
