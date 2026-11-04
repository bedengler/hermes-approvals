"""Install or remove the approval-cleanup job in one explicit Hermes profile."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

JOB_NAME = "approvals-retention-cleanup"
SCRIPT_NAME = "approval-cleanup.py"
DEFAULT_SCHEDULE = "0 3 * * *"


class InstallerError(RuntimeError):
    """Raised when the selected profile cannot be changed safely."""


def _profile_home(hermes_home: Path, profile: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", profile):
        raise InstallerError("profile must be a simple profile name")
    return hermes_home if profile == "default" else hermes_home / "profiles" / profile


def _load_jobs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        jobs = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallerError(f"cannot read cron job store {path}: {exc}") from exc
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        raise InstallerError(f"cron job store {path} must contain a JSON list of objects")
    return jobs


def _is_our_job(job: dict[str, Any], interpreter: str, schedule: str) -> bool:
    return (
        job.get("name") == JOB_NAME
        and job.get("no_agent") is True
        and job.get("script") == SCRIPT_NAME
        and job.get("interpreter") == interpreter
        and job.get("schedule_display") == schedule
        and job.get("deliver") == "local"
    )


def _named_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [job for job in jobs if job.get("name") == JOB_NAME]


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(source.read_bytes())
        os.chmod(temp, source.stat().st_mode & 0o777)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _command_prefix(hermes: Path, profile: str) -> list[str]:
    prefix = [str(hermes)]
    if profile != "default":
        prefix.extend(["-p", profile])
    return prefix


def _run_hermes(
    args: argparse.Namespace,
    command: list[str],
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    root = args.hermes_home.expanduser().resolve()
    env["HERMES_HOME"] = str(root)
    env["HOME"] = str(root.parent)
    env["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    try:
        return subprocess.run(command, check=False, text=True, capture_output=True, env=env, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise InstallerError(f"Hermes CLI timed out after 60 seconds: {args.hermes}") from exc
    except OSError as exc:
        raise InstallerError(f"could not run Hermes CLI {args.hermes}: {exc}") from exc


def _verify_job_store(jobs_path: Path, interpreter: str, schedule: str) -> dict[str, Any] | None:
    named = _named_jobs(_load_jobs(jobs_path))
    return named[0] if len(named) == 1 and _is_our_job(named[0], interpreter, schedule) else None


def install(args: argparse.Namespace) -> str:
    root = args.hermes_home.expanduser().resolve()
    profile_home = _profile_home(root, args.profile)
    source = args.script_source.expanduser().resolve()
    hermes = args.hermes.expanduser().resolve()
    if not source.is_file():
        raise InstallerError(f"cleanup script source is not a file: {source}")
    if not hermes.is_file():
        raise InstallerError(f"Hermes CLI is not a file: {hermes}")
    if not args.interpreter.strip():
        raise InstallerError("interpreter must not be empty")

    jobs_path = profile_home / "cron" / "jobs.json"
    script_path = profile_home / "scripts" / SCRIPT_NAME
    jobs = _load_jobs(jobs_path)
    named = _named_jobs(jobs)
    if named:
        if len(named) == 1 and _is_our_job(named[0], args.interpreter, args.schedule):
            if not script_path.is_file() or script_path.read_bytes() != source.read_bytes():
                raise InstallerError("the registered cleanup job exists but its script file differs from the source")
            return f"Already installed in profile {args.profile}: {named[0]['id']}"
        raise InstallerError(f"profile {args.profile} already has a conflicting job named {JOB_NAME}")

    if script_path.exists() and script_path.read_bytes() != source.read_bytes():
        raise InstallerError(f"refusing to overwrite a different file: {script_path}")
    if args.dry_run:
        return (
            f"Would install {JOB_NAME} in profile {args.profile}; script={script_path}; "
            f"schedule={args.schedule}; interpreter={args.interpreter}"
        )

    script_created = not script_path.exists()
    if script_created:
        _atomic_copy(source, script_path)

    command = [
        *_command_prefix(hermes, args.profile),
        "cron", "create", args.schedule,
        "--name", JOB_NAME,
        "--deliver", "local",
        "--no-agent",
        "--script", SCRIPT_NAME,
        "--interpreter", args.interpreter,
    ]
    try:
        result = _run_hermes(args, command)
        registered = _verify_job_store(jobs_path, args.interpreter, args.schedule)
        if result.returncode != 0 or registered is None:
            raise InstallerError(
                f"Hermes did not register the expected cleanup job for profile {args.profile} "
                f"(exit={result.returncode})"
            )
    except Exception:
        if script_created and _verify_job_store(jobs_path, args.interpreter, args.schedule) is None:
            script_path.unlink(missing_ok=True)
        raise
    return f"Installed in profile {args.profile}: {registered['id']}"


def uninstall(args: argparse.Namespace) -> str:
    root = args.hermes_home.expanduser().resolve()
    profile_home = _profile_home(root, args.profile)
    hermes = args.hermes.expanduser().resolve()
    if not hermes.is_file():
        raise InstallerError(f"Hermes CLI is not a file: {hermes}")
    jobs_path = profile_home / "cron" / "jobs.json"
    jobs = _load_jobs(jobs_path)
    named = _named_jobs(jobs)
    if not named:
        return f"No {JOB_NAME} job found in profile {args.profile}"
    if len(named) != 1 or not _is_our_job(named[0], args.interpreter, args.schedule):
        raise InstallerError(f"profile {args.profile} has a conflicting {JOB_NAME} job; leaving it unchanged")
    job = named[0]
    script_path = profile_home / "scripts" / SCRIPT_NAME
    if args.dry_run:
        return f"Would remove only {JOB_NAME} ({job['id']}) from profile {args.profile}"

    command = [*_command_prefix(hermes, args.profile), "cron", "remove", str(job["id"])]
    result = _run_hermes(args, command)
    remaining = _load_jobs(jobs_path)
    if result.returncode != 0 or any(item.get("id") == job.get("id") for item in remaining):
        raise InstallerError(
            f"Hermes did not remove cleanup job {job.get('id')} from profile {args.profile} "
            f"(exit={result.returncode})"
        )

    still_referenced = any(item.get("script") == SCRIPT_NAME for item in remaining)
    if script_path.is_file() and not still_referenced and args.script_source.is_file():
        if script_path.read_bytes() == args.script_source.read_bytes():
            script_path.unlink()
    return f"Removed only {JOB_NAME} ({job['id']}) from profile {args.profile}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--profile", required=True, help="explicit Hermes profile name (use 'default' for the default profile)")
    parser.add_argument("--hermes-home", type=Path, required=True, help="Hermes home/root containing the selected profile")
    parser.add_argument("--hermes", type=Path, required=True, help="path to the Hermes CLI executable")
    parser.add_argument("--script-source", type=Path, required=True, help="path to dashboard/approval_cleanup.py")
    parser.add_argument("--interpreter", required=True, help="Python interpreter used by the no-agent cron job")
    parser.add_argument("--schedule", default=DEFAULT_SCHEDULE, help=f"cron schedule (default: {DEFAULT_SCHEDULE})")
    parser.add_argument("--dry-run", action="store_true", help="show the change without writing files or invoking Hermes")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        message = install(args) if args.action == "install" else uninstall(args)
    except InstallerError as exc:
        print(f"Cleanup installer failed: {exc}", file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
