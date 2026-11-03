"""Exercise approval cleanup through Hermes' real per-profile cron script path.

Run with HERMES_AGENT_REPO pointing at a supported Hermes Agent checkout.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from hermes_approval.store import ApprovalStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _seed_store(home: Path, profile: str) -> None:
    store = ApprovalStore(home / "approvals.db", profile=profile)
    store.create(session_id="pending-session", explanation="pending", source="test")
    old = store.create(session_id="old-session", explanation="old", source="test")
    store.respond(old.request_id, "deny", expected_version=1)
    recent = store.create(session_id="recent-session", explanation="recent", source="test")
    store.respond(recent.request_id, "approve", expected_version=1)
    with sqlite3.connect(home / "approvals.db") as db:
        db.execute(
            "UPDATE approvals SET resolved_at=? WHERE request_id=? AND profile=?",
            (time.time() - 36 * 60 * 60, old.request_id, profile),
        )


def test_supported_hermes_cron_cleans_each_profile_with_configured_retention(tmp_path, monkeypatch):
    host_repo_value = os.environ.get("HERMES_AGENT_REPO")
    if host_repo_value is None:
        pytest.skip("set HERMES_AGENT_REPO and run in that Hermes Agent uv environment")
    host_repo = Path(host_repo_value).resolve()
    assert (host_repo / "cron" / "scheduler.py").is_file()

    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "1")
    source_paths = [str(PROJECT_ROOT / "src"), str(PROJECT_ROOT), str(host_repo)]
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    if inherited_pythonpath:
        source_paths.append(inherited_pythonpath)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(source_paths))
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "src"))
    monkeypatch.syspath_prepend(str(host_repo))

    profiles = {
        "alpha": {"retention_days": 1, "max_events": 3},
        "beta": {"retention_days": 2, "max_events": 4},
        "broken": {"retention_days": 0, "max_events": 3},
    }
    profile_homes = {}
    for profile, retention in profiles.items():
        home = hermes_home / "profiles" / profile
        (home / "scripts").mkdir(parents=True)
        (home / "config.yaml").write_text(
            "approvals:\n"
            f"  retention_days: {retention['retention_days']}\n"
            f"  max_events: {retention['max_events']}\n",
            encoding="utf-8",
        )
        shutil.copyfile(
            PROJECT_ROOT / "dashboard" / "approval_cleanup.py",
            home / "scripts" / "approval-cleanup.py",
        )
        _seed_store(home, profile)
        profile_homes[profile] = home

    # The host's supported no-agent cron job runner executes the copied script
    # under the scoped home just as the multiplex scheduler does for each profile.
    from cron.scheduler import run_job
    from cron.scheduler_provider import _profile_cron_scope

    for profile in ("alpha", "beta"):
        with _profile_cron_scope(profile_homes[profile]):
            ok, output, _response, error = run_job(
                {
                    "id": f"approval-cleanup-{profile}",
                    "name": "approval-retention-cleanup",
                    "no_agent": True,
                    "script": "approval-cleanup.py",
                    "interpreter": sys.executable,
                }
            )
        assert ok, error
        assert f"profile={profile}" in output
        store = ApprovalStore(profile_homes[profile] / "approvals.db", profile=profile)
        assert [item.session_id for item in store.list_pending()] == ["pending-session"]
        history = {item.session_id for item in store.history()}
        if profile == "alpha":
            assert history == {"recent-session"}
        else:
            assert history == {"old-session", "recent-session"}
        assert len(store.events(limit=100)) == profiles[profile]["max_events"]

    with _profile_cron_scope(profile_homes["broken"]):
        ok, output, response, error = run_job(
            {
                "id": "approval-cleanup-broken",
                "name": "approval-retention-cleanup",
                "no_agent": True,
                "script": "approval-cleanup.py",
                "interpreter": sys.executable,
            }
        )
    assert not ok
    assert "retention_days" in (error or output or response)
