from __future__ import annotations

import json
import os
import shutil
import subprocess
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


def test_actual_hermes_cli_installs_and_removes_only_in_temp_profile(tmp_path):
    hermes_executable = shutil.which("hermes")
    if not hermes_executable:
        pytest.skip("Hermes CLI is not installed; fake-runner tests cover installer safety")
    assert hermes_executable
    root = tmp_path / ".hermes"
    args = _args(root)
    args.hermes = Path(hermes_executable)
    args.script_source = SCRIPT_SOURCE
    env = {
        **os.environ,
        "HOME": str(root.parent),
        "HERMES_HOME": str(root),
        "HERMES_DISABLE_LAZY_INSTALLS": "1",
    }
    profile_creation = subprocess.run(
        [hermes_executable, "profile", "create", "alpha", "--no-alias", "--no-skills"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    if profile_creation.returncode and "No module named 'ruamel'" in profile_creation.stderr:
        pytest.skip("installed Hermes CLI environment is missing ruamel.yaml")
    assert profile_creation.returncode == 0, profile_creation.stderr

    assert "Installed" in cleanup_installer.install(args)
    home = root / "profiles" / "alpha"
    jobs = json.loads((home / "cron" / "jobs.json").read_text(encoding="utf-8"))
    assert len(jobs) == 1
    assert jobs[0]["no_agent"] is True
    assert jobs[0]["script"] == cleanup_installer.SCRIPT_NAME
    assert jobs[0]["schedule_display"] == args.schedule
    assert "Already installed" in cleanup_installer.install(args)

    args.action = "uninstall"
    assert "Removed only" in cleanup_installer.uninstall(args)
    assert json.loads((home / "cron" / "jobs.json").read_text(encoding="utf-8")) == []
    assert not (home / "scripts" / cleanup_installer.SCRIPT_NAME).exists()
