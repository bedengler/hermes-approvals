from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from hermes_approval import cleanup_installer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SOURCE = PROJECT_ROOT / "dashboard" / "approval_cleanup.py"


def _args(root: Path, profile: str = "alpha", *, dry_run: bool = False) -> Namespace:
    found_hermes = shutil.which("hermes")
    hermes = Path(found_hermes) if found_hermes else root.parent / "hermes-test-command"
    if not hermes.exists():
        hermes.parent.mkdir(parents=True, exist_ok=True)
        hermes.write_text("test hermes command", encoding="utf-8")
    return Namespace(
        action="install",
        profile=profile,
        hermes_home=root,
        hermes=hermes,
        script_source=SCRIPT_SOURCE,
        interpreter="/tmp/profile-venv/bin/python",
        schedule=cleanup_installer.DEFAULT_SCHEDULE,
        dry_run=dry_run,
    )


def test_dry_run_makes_no_files_and_never_invokes_hermes(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.py"
    source.write_text("cleanup fixture", encoding="utf-8")
    root = tmp_path / "homes"
    args = _args(root)
    args.script_source = source
    args.dry_run = True
    monkeypatch.setattr(cleanup_installer, "_run_hermes", lambda *a, **kw: pytest.fail("dry-run invoked Hermes"))

    result = cleanup_installer.main([
        "install",
        "--profile", args.profile,
        "--hermes-home", str(args.hermes_home),
        "--hermes", str(args.hermes),
        "--script-source", str(args.script_source),
        "--interpreter", args.interpreter,
        "--dry-run",
    ])

    assert result == 0
    assert "Would install" in capsys.readouterr().out
    assert not root.exists()


def test_install_is_idempotent_and_uninstall_removes_only_its_job(tmp_path, monkeypatch):
    source = tmp_path / "source.py"
    source.write_text("cleanup fixture", encoding="utf-8")
    root = tmp_path / "homes"
    target_home = root / "profiles" / "alpha"
    other_home = root / "profiles" / "beta"
    unrelated = {"id": "unrelated", "name": "keep-me", "script": "other.py"}
    other_home.joinpath("cron").mkdir(parents=True)
    (other_home / "cron" / "jobs.json").write_text(json.dumps([unrelated]), encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        env = kwargs["env"]
        assert kwargs["timeout"] == 60
        hermes_home = Path(env["HERMES_HOME"])
        profile = command[command.index("-p") + 1] if "-p" in command else "default"
        profile_home = hermes_home if profile == "default" else hermes_home / "profiles" / profile
        calls.append((profile_home, command))
        store = profile_home / "cron" / "jobs.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        jobs = json.loads(store.read_text(encoding="utf-8")) if store.exists() else []
        action_index = command.index("cron") + 1
        if command[action_index] == "create":
            jobs.append({
                "id": "approval-cleanup-id",
                "name": command[command.index("--name") + 1],
                "no_agent": "--no-agent" in command,
                "script": command[command.index("--script") + 1],
                "interpreter": command[command.index("--interpreter") + 1],
                "schedule_display": command[action_index + 1],
                "deliver": command[command.index("--deliver") + 1],
            })
        elif command[action_index] == "remove":
            remove_id = command[-1]
            jobs = [job for job in jobs if job.get("id") != remove_id]
        store.write_text(json.dumps(jobs), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(cleanup_installer.subprocess, "run", fake_run)
    args = _args(root)
    args.script_source = source
    assert "Installed" in cleanup_installer.install(args)
    assert "Already installed" in cleanup_installer.install(args)
    assert len(calls) == 1
    installed = json.loads((target_home / "cron" / "jobs.json").read_text(encoding="utf-8"))
    assert [job["name"] for job in installed] == [cleanup_installer.JOB_NAME]
    assert (target_home / "scripts" / cleanup_installer.SCRIPT_NAME).read_text(encoding="utf-8") == "cleanup fixture"
    assert json.loads((other_home / "cron" / "jobs.json").read_text(encoding="utf-8")) == [unrelated]

    args.action = "uninstall"
    assert "Removed only" in cleanup_installer.uninstall(args)
    remaining = json.loads((target_home / "cron" / "jobs.json").read_text(encoding="utf-8"))
    assert remaining == []
    assert not (target_home / "scripts" / cleanup_installer.SCRIPT_NAME).exists()
    assert json.loads((other_home / "cron" / "jobs.json").read_text(encoding="utf-8")) == [unrelated]


def test_conflicting_named_job_is_preserved(tmp_path, monkeypatch):
    root = tmp_path / "homes"
    home = root / "profiles" / "alpha"
    jobs_path = home / "cron" / "jobs.json"
    jobs_path.parent.mkdir(parents=True)
    existing = {"id": "owned-by-operator", "name": cleanup_installer.JOB_NAME, "script": "different.py"}
    jobs_path.write_text(json.dumps([existing]), encoding="utf-8")
    args = _args(root)
    monkeypatch.setattr(cleanup_installer, "_run_hermes", lambda *a, **kw: pytest.fail("unexpected invocation"))

    with pytest.raises(cleanup_installer.InstallerError):
        cleanup_installer.install(args)
    assert json.loads(jobs_path.read_text(encoding="utf-8")) == [existing]


def _real_cli_prerequisites() -> tuple[Path, str]:
    """Return the Hermes Agent checkout and uv executable, or skip.

    CI has neither a checkout nor HERMES_AGENT_REPO; skipping there keeps the
    suite green while the documented local command still executes the real CLI.
    """
    hermes_repo = Path(
        os.environ.get(
            "HERMES_AGENT_REPO",
            str(Path.home() / ".hermes" / "hermes-agent"),
        )
    ).expanduser().resolve()
    if not (hermes_repo / "pyproject.toml").is_file():
        pytest.skip(
            "set HERMES_AGENT_REPO to a Hermes Agent source checkout to run the "
            "real CLI installer test"
        )
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        pytest.skip("uv is required to run Hermes in an isolated test environment")
    return hermes_repo, uv_executable


def test_real_cli_installs_idempotently_and_removes_only_its_temp_profile_job(tmp_path):
    hermes_repo, uv_executable = _real_cli_prerequisites()

    root = tmp_path / ".hermes"
    hermes_executable = tmp_path / "hermes-real-cli"
    hermes_executable.write_text(
        "#!/bin/sh\n"
        "exec "
        + shlex.quote(uv_executable)
        + " run --locked --project "
        + shlex.quote(str(hermes_repo))
        + " --with ruamel.yaml==0.18.16 python -m hermes_cli.main \"$@\"\n",
        encoding="utf-8",
    )
    hermes_executable.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(root.parent),
        "HERMES_HOME": str(root),
        "HERMES_DISABLE_LAZY_INSTALLS": "1",
        "UV_PROJECT_ENVIRONMENT": str(tmp_path / "hermes-cli-venv"),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
    }

    def run_cli(*command: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [str(hermes_executable), *command],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        assert result.returncode == 0, f"Hermes CLI failed: {result.stderr}"
        return result

    for profile in ("alpha", "beta"):
        run_cli("profile", "create", profile, "--no-alias", "--no-skills")

    beta_home = root / "profiles" / "beta"
    neighbor_script = beta_home / "scripts" / "neighbor.py"
    neighbor_script.parent.mkdir(parents=True, exist_ok=True)
    neighbor_script.write_text("print('neighbor')\n", encoding="utf-8")
    run_cli(
        "-p",
        "beta",
        "cron",
        "create",
        "17 4 * * *",
        "--name",
        "neighbor-maintenance",
        "--deliver",
        "local",
        "--no-agent",
        "--script",
        "neighbor.py",
        "--interpreter",
        sys.executable,
    )
    neighbor_jobs_path = beta_home / "cron" / "jobs.json"
    neighbor_jobs_before = json.loads(neighbor_jobs_path.read_text(encoding="utf-8"))
    neighbor_script_before = neighbor_script.read_bytes()

    home = root / "profiles" / "alpha"
    sibling_script = home / "scripts" / "sibling.py"
    sibling_script.parent.mkdir(parents=True, exist_ok=True)
    sibling_script.write_text("print('sibling')\n", encoding="utf-8")
    run_cli(
        "-p",
        "alpha",
        "cron",
        "create",
        "31 5 * * *",
        "--name",
        "sibling-maintenance",
        "--deliver",
        "local",
        "--no-agent",
        "--script",
        "sibling.py",
        "--interpreter",
        sys.executable,
    )
    jobs_path = home / "cron" / "jobs.json"
    sibling_job = next(
        job
        for job in json.loads(jobs_path.read_text(encoding="utf-8"))["jobs"]
        if job["name"] == "sibling-maintenance"
    )
    sibling_script_before = sibling_script.read_bytes()

    args = Namespace(
        action="install",
        profile="alpha",
        hermes_home=root,
        hermes=hermes_executable,
        script_source=SCRIPT_SOURCE,
        interpreter=sys.executable,
        schedule=cleanup_installer.DEFAULT_SCHEDULE,
        dry_run=False,
    )
    script_path = home / "scripts" / cleanup_installer.SCRIPT_NAME

    assert "Installed" in cleanup_installer.install(args)
    installed_document = json.loads(jobs_path.read_text(encoding="utf-8"))
    installed = installed_document["jobs"]
    cleanup_jobs = [
        job for job in installed if job["name"] == cleanup_installer.JOB_NAME
    ]
    assert len(cleanup_jobs) == 1
    assert cleanup_jobs[0]["no_agent"] is True
    assert cleanup_jobs[0]["script"] == cleanup_installer.SCRIPT_NAME
    assert cleanup_jobs[0]["schedule_display"] == args.schedule
    assert sibling_job in installed
    assert script_path.read_bytes() == SCRIPT_SOURCE.read_bytes()

    assert "Already installed" in cleanup_installer.install(args)
    assert json.loads(jobs_path.read_text(encoding="utf-8")) == installed_document
    assert script_path.read_bytes() == SCRIPT_SOURCE.read_bytes()

    args.action = "uninstall"
    assert "Removed only" in cleanup_installer.uninstall(args)
    remaining = json.loads(jobs_path.read_text(encoding="utf-8"))["jobs"]
    assert remaining == [sibling_job]
    assert sibling_script.read_bytes() == sibling_script_before
    assert not script_path.exists()
    assert json.loads(neighbor_jobs_path.read_text(encoding="utf-8")) == neighbor_jobs_before
    assert neighbor_script.read_bytes() == neighbor_script_before


def test_installer_accepts_the_canonical_jobs_document_format(tmp_path, monkeypatch):
    """Hermes stores cron jobs as {"jobs": [...]}; the installer must read and extend it."""
    home = tmp_path / "homes" / "profiles" / "alpha"
    jobs_path = home / "cron" / "jobs.json"
    jobs_path.parent.mkdir(parents=True)
    sibling = {"id": "sibling-id", "name": "sibling-maintenance", "script": "sibling.py"}
    document = {"jobs": [sibling], "updated_at": "2026-11-05T00:00:00"}
    jobs_path.write_text(json.dumps(document), encoding="utf-8")

    def fake_run_hermes(args, command):
        store = json.loads(jobs_path.read_text(encoding="utf-8"))
        store["jobs"].append(
            {
                "id": "approval-cleanup-id",
                "name": cleanup_installer.JOB_NAME,
                "no_agent": True,
                "script": cleanup_installer.SCRIPT_NAME,
                "interpreter": args.interpreter,
                "schedule_display": args.schedule,
                "deliver": "local",
            }
        )
        jobs_path.write_text(json.dumps(store), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(cleanup_installer, "_run_hermes", fake_run_hermes)
    args = _args(tmp_path / "homes")

    assert "Installed" in cleanup_installer.install(args)
    installed = json.loads(jobs_path.read_text(encoding="utf-8"))
    assert installed["updated_at"] == document["updated_at"]
    assert installed["jobs"][0] == sibling
    assert [job["name"] for job in installed["jobs"]] == [
        sibling["name"],
        cleanup_installer.JOB_NAME,
    ]
    assert "Already installed" in cleanup_installer.install(args)


def test_conflicting_job_inside_the_canonical_document_is_preserved(tmp_path, monkeypatch):
    home = tmp_path / "homes" / "profiles" / "alpha"
    jobs_path = home / "cron" / "jobs.json"
    jobs_path.parent.mkdir(parents=True)
    conflicting = {"id": "owned-by-operator", "name": cleanup_installer.JOB_NAME, "script": "different.py"}
    document = {"jobs": [conflicting]}
    jobs_path.write_text(json.dumps(document), encoding="utf-8")
    args = _args(tmp_path / "homes")
    monkeypatch.setattr(cleanup_installer, "_run_hermes", lambda *a, **kw: pytest.fail("unexpected invocation"))

    with pytest.raises(cleanup_installer.InstallerError):
        cleanup_installer.install(args)
    assert json.loads(jobs_path.read_text(encoding="utf-8")) == document
