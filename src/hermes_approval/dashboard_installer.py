"""Install, upgrade, or roll back the approvals dashboard in one Hermes profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PLUGIN_NAME = "approvals"
STATE_DIR_NAME = ".hermes-approval-api-installer"
STATE_FILE_NAME = "state.json"


class InstallerError(RuntimeError):
    """Raised when the selected profile cannot be changed safely."""


def _paths(hermes_home: Path, profile: str) -> tuple[Path, Path, Path]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", profile):
        raise InstallerError("profile must be a simple profile name")
    root = hermes_home.expanduser().resolve()
    profile_home = root if profile == "default" else root / "profiles" / profile
    plugin_root = profile_home / "plugins" / PLUGIN_NAME
    dashboard = plugin_root / "dashboard"
    state_dir = plugin_root / STATE_DIR_NAME
    for path in (
        profile_home,
        profile_home / "plugins",
        plugin_root,
        dashboard,
        state_dir,
        state_dir / "snapshots",
        state_dir / STATE_FILE_NAME,
    ):
        _reject_symlink(path, root)
    return profile_home, dashboard, state_dir


def _reject_symlink(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise InstallerError(
            "selected profile path escapes the explicit Hermes home"
        ) from exc
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise InstallerError(
                f"refusing to follow a symlink in the selected profile path: {cursor}"
            )


def _validate_tree(path: Path) -> None:
    if path.is_symlink():
        raise InstallerError(f"refusing a symlinked dashboard directory: {path}")
    if not path.is_dir():
        raise InstallerError(f"dashboard payload is not a directory: {path}")
    for child in path.rglob("*"):
        if child.is_symlink():
            raise InstallerError(f"refusing a symlink in dashboard payload: {child}")
        if not (child.is_dir() or child.is_file()):
            raise InstallerError(f"unsupported dashboard payload entry: {child}")


def _manifest(path: Path) -> dict[str, Any]:
    manifest_file = path / "manifest.json"
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallerError(
            f"cannot read dashboard manifest {manifest_file}: {exc}"
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("name") != PLUGIN_NAME:
        raise InstallerError(
            f"refusing dashboard payload without manifest name {PLUGIN_NAME!r}: {manifest_file}"
        )
    return manifest


def _digest(path: Path) -> str | None:
    if not path.exists():
        return None
    _validate_tree(path)
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_dir() or "__pycache__" in child.parts or child.suffix == ".pyc":
            continue
        relative = child.relative_to(path).as_posix().encode("utf-8")
        digest.update(relative + b"\0")
        digest.update(child.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _source_payload(source: Path) -> tuple[Path, dict[str, Any]]:
    resolved = source.expanduser().resolve()
    if not resolved.is_dir():
        raise InstallerError(f"dashboard source is not a directory: {resolved}")
    _validate_tree(resolved)
    manifest = _manifest(resolved)
    return resolved, manifest


def _load_state(state_file: Path) -> dict[str, Any] | None:
    if not state_file.exists():
        return None
    _reject_symlink(state_file, state_file.parent)
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallerError(
            f"cannot read installer state {state_file}: {exc}"
        ) from exc
    if (
        not isinstance(state, dict)
        or state.get("format") != 1
        or not isinstance(state.get("history"), list)
        or not isinstance(state.get("current_digest"), (str, type(None)))
    ):
        raise InstallerError(f"invalid installer state: {state_file}")
    for record in state["history"]:
        if not isinstance(record, dict) or not isinstance(record.get("present"), bool):
            raise InstallerError(f"invalid rollback history in {state_file}")
        snapshot = record.get("snapshot")
        if record["present"]:
            if not isinstance(snapshot, str) or not re.fullmatch(
                r"[0-9a-f]{32}", snapshot
            ):
                raise InstallerError(
                    f"invalid rollback snapshot identifier in {state_file}"
                )
        elif snapshot is not None:
            raise InstallerError(
                f"invalid absent-state rollback record in {state_file}"
            )
    return state


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _stage_copy(source: Path, plugin_root: Path, previous: Path | None = None) -> Path:
    stage = Path(
        tempfile.mkdtemp(prefix=".approvals-dashboard-stage-", dir=plugin_root)
    )
    try:
        if previous is not None:
            shutil.copytree(
                previous,
                stage,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        shutil.copytree(
            source,
            stage,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return stage


def _payload_matches(source: Path, target: Path) -> bool:
    for source_file in source.rglob("*"):
        if (
            source_file.is_dir()
            or "__pycache__" in source_file.parts
            or source_file.suffix == ".pyc"
        ):
            continue
        target_file = target / source_file.relative_to(source)
        if (
            not target_file.is_file()
            or target_file.read_bytes() != source_file.read_bytes()
        ):
            return False
    return True


def _current_check(dashboard: Path, state: dict[str, Any] | None) -> str | None:
    current = _digest(dashboard)
    if state is not None and current != state["current_digest"]:
        raise InstallerError(
            "installed dashboard payload changed since the installer recorded it; "
            "leaving operator changes untouched"
        )
    if dashboard.exists():
        _manifest(dashboard)
    return current


def _payload_differences(source: Path, target: Path) -> dict[str, list[str]]:
    expected_files = {
        child.relative_to(source).as_posix(): child
        for child in source.rglob("*")
        if child.is_file()
        and "__pycache__" not in child.parts
        and child.suffix != ".pyc"
    }
    actual_files = {
        child.relative_to(target).as_posix(): child
        for child in target.rglob("*")
        if child.is_file()
        and "__pycache__" not in child.parts
        and child.suffix != ".pyc"
    }
    missing = sorted(expected_files.keys() - actual_files.keys())
    unexpected = sorted(actual_files.keys() - expected_files.keys())
    changed = sorted(
        relative
        for relative in expected_files.keys() & actual_files.keys()
        if expected_files[relative].read_bytes() != actual_files[relative].read_bytes()
    )
    return {
        "missing_files": missing,
        "changed_files": changed,
        "unexpected_files": unexpected,
    }


def status(hermes_home: Path, profile: str, source: Path) -> dict[str, Any]:
    """Report the selected profile's dashboard state without modifying it."""
    _, dashboard, state_dir = _paths(hermes_home, profile)
    source_dir, _ = _source_payload(source)
    state = _load_state(state_dir / STATE_FILE_NAME)
    report: dict[str, Any] = {
        "status": "absent",
        "profile": profile,
        "target": str(dashboard),
        "missing_files": [],
        "changed_files": [],
        "unexpected_files": [],
        "reasons": [],
    }

    if not dashboard.exists():
        if state is not None:
            report["status"] = "drift"
            report["missing_files"] = ["<dashboard directory>"]
            report["reasons"] = ["installer state exists but the dashboard directory is missing"]
        return report

    _validate_tree(dashboard)
    report.update(_payload_differences(source_dir, dashboard))
    if state is None:
        report["status"] = "drift"
        report["reasons"].append("dashboard exists without installer state")
    elif _digest(dashboard) != state["current_digest"]:
        report["status"] = "drift"
        report["reasons"].append("dashboard differs from the installer-recorded digest")

    if report["missing_files"] or report["changed_files"]:
        report["status"] = "drift"
        report["reasons"].append("dashboard files differ from the selected package source")
    if report["status"] != "drift":
        report["status"] = "installed-and-matching"
    return report


def install(
    hermes_home: Path, profile: str, source: Path, *, dry_run: bool = False
) -> str:
    _, dashboard, state_dir = _paths(hermes_home, profile)
    plugin_root = dashboard.parent
    source_dir, manifest = _source_payload(source)
    state_file = state_dir / STATE_FILE_NAME
    state = _load_state(state_file)
    current = _current_check(dashboard, state)
    if (
        current is not None
        and state is not None
        and _payload_matches(source_dir, dashboard)
    ):
        return f"Already installed in profile {profile}: version {manifest.get('version', 'unknown')}"

    if dashboard.exists() and state is None:
        _manifest(dashboard)
    if dry_run:
        operation = "upgrade" if dashboard.exists() else "install"
        return (
            f"Would {operation} {PLUGIN_NAME} dashboard in profile {profile}; "
            f"target={dashboard}; version={manifest.get('version', 'unknown')}"
        )

    plugin_root.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    snapshots = state_dir / "snapshots"
    snapshots.mkdir(exist_ok=True)
    history = list(state["history"]) if state is not None else []
    snapshot_id = uuid.uuid4().hex
    if dashboard.exists():
        snapshot_path = snapshots / snapshot_id
        _validate_tree(dashboard)
        try:
            shutil.copytree(dashboard, snapshot_path)
        except Exception:
            shutil.rmtree(snapshot_path, ignore_errors=True)
            raise
        history.append({"present": True, "snapshot": snapshot_id})
    else:
        history.append({"present": False, "snapshot": None})

    stage: Path | None = None
    displaced = plugin_root / f".approvals-dashboard-previous-{uuid.uuid4().hex}"
    old_moved = False
    new_moved = False
    try:
        stage = _stage_copy(
            source_dir, plugin_root, dashboard if dashboard.exists() else None
        )
        if dashboard.exists():
            os.replace(dashboard, displaced)
            old_moved = True
        os.replace(stage, dashboard)
        stage = None
        new_moved = True
        next_state = {
            "format": 1,
            "current_digest": _digest(dashboard),
            "history": history,
        }
        _atomic_json(state_file, next_state)
    except Exception:
        if new_moved and dashboard.exists():
            shutil.rmtree(dashboard)
        if old_moved and displaced.exists():
            os.replace(displaced, dashboard)
        snapshot_path = snapshots / snapshot_id
        if snapshot_path.exists():
            shutil.rmtree(snapshot_path)
        raise
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
    if displaced.exists():
        shutil.rmtree(displaced, ignore_errors=True)
    operation = "Upgraded" if current is not None else "Installed"
    return f"{operation} {PLUGIN_NAME} dashboard in profile {profile}: version {manifest.get('version', 'unknown')}"


def rollback(hermes_home: Path, profile: str) -> str:
    _, dashboard, state_dir = _paths(hermes_home, profile)
    plugin_root = dashboard.parent
    state_file = state_dir / STATE_FILE_NAME
    state = _load_state(state_file)
    if state is None or not state["history"]:
        raise InstallerError(
            f"no dashboard installer rollback is available for profile {profile}"
        )
    _current_check(dashboard, state)
    record = state["history"][-1]
    snapshot_path = (
        state_dir / "snapshots" / record["snapshot"] if record["present"] else None
    )
    if snapshot_path is not None:
        _reject_symlink(snapshot_path, snapshot_path.parent)
        _validate_tree(snapshot_path)
        _manifest(snapshot_path)

    stage: Path | None = None
    if snapshot_path is not None:
        stage = _stage_copy(snapshot_path, plugin_root)
    displaced = plugin_root / f".approvals-dashboard-rollback-{uuid.uuid4().hex}"
    old_moved = False
    new_moved = False
    try:
        if dashboard.exists():
            os.replace(dashboard, displaced)
            old_moved = True
        if stage is not None:
            os.replace(stage, dashboard)
            stage = None
            new_moved = True
        history = list(state["history"][:-1])
        restored_digest = _digest(dashboard)
        _atomic_json(
            state_file,
            {"format": 1, "current_digest": restored_digest, "history": history},
        )
    except Exception:
        if new_moved and dashboard.exists():
            shutil.rmtree(dashboard)
        if old_moved and displaced.exists():
            os.replace(displaced, dashboard)
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        raise
    if snapshot_path is not None and snapshot_path.exists():
        shutil.rmtree(snapshot_path, ignore_errors=True)
    if displaced.exists():
        shutil.rmtree(displaced, ignore_errors=True)
    return (
        f"Rolled back the most recent approvals dashboard change in profile {profile}"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "rollback", "status"))
    parser.add_argument(
        "--profile",
        required=True,
        help="explicit Hermes profile (use 'default' for the default profile)",
    )
    parser.add_argument(
        "--hermes-home",
        type=Path,
        required=True,
        help="explicit Hermes root containing the selected profile",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="dashboard package directory containing manifest.json (required for install/status)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show the planned install/upgrade without creating or changing files",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "install":
            if args.source is None:
                raise InstallerError("--source is required for install")
            message = install(
                args.hermes_home, args.profile, args.source, dry_run=args.dry_run
            )
        elif args.action == "rollback":
            if args.dry_run:
                raise InstallerError(
                    "--dry-run is supported for install; rollback only accepts an explicit action"
                )
            message = rollback(args.hermes_home, args.profile)
        else:
            if args.source is None:
                raise InstallerError("--source is required for status")
            if args.dry_run:
                raise InstallerError("--dry-run is not applicable to status")
            report = status(args.hermes_home, args.profile, args.source)
            print(json.dumps(report, sort_keys=True))
            return 0
    except (InstallerError, OSError, shutil.Error) as exc:
        print(f"Dashboard installer failed: {exc}", file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
