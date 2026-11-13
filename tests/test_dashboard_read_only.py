from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.plugin_api import build_router
from hermes_approval.store import ApprovalStore


TOKEN = "synthetic-dashboard-token"


def test_dashboard_adapter_reads_synthetic_pending_and_history_read_only(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    ledger = tmp_path / "approvals.jsonl"
    monkeypatch.setenv("HERMES_GOVERNANCE_HOME", str(tmp_path))
    rows = [
        {
            "approval_id": "fixture-pending",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "gate": "test-gate",
            "action": "inspect-fixture",
            "target": "synthetic-target",
            "rationale": "Bearer FIXTURE-SECRET",
            "status": "pending",
            "session_id": "fixture-private-session",
            "private_extension": {"credential": "FIXTURE-SECRET"},
        },
        {
            "approval_id": "fixture-history",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "gate": "test-gate",
            "action": "inspect-fixture",
            "target": "synthetic-target",
            "rationale": "synthetic history",
            "status": "approved",
        },
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    store = ApprovalStore(tmp_path / "profile-a.db", profile="profile-a")
    created = store.create(session_id="fixture-session", explanation="synthetic pending", source="test")
    store.respond(created.request_id, "approve", expected_version=1)
    pending = store.create(session_id="fixture-session", explanation="still pending", source="test")

    app = FastAPI()
    app.include_router(
        build_router(store, lambda connection: connection.headers.get("X-Test-Auth") == TOKEN),
        prefix="/api/plugins/approvals",
    )
    client = TestClient(app)

    assert client.get("/api/plugins/approvals/pending").status_code == 403
    assert client.get("/api/plugins/approvals/governance/pending").status_code == 403
    headers = {"X-Test-Auth": TOKEN}
    runtime_pending = client.get("/api/plugins/approvals/pending", headers=headers)
    runtime_history = client.get("/api/plugins/approvals/history", headers=headers)
    governance_pending = client.get("/api/plugins/approvals/governance/pending", headers=headers)
    governance_history = client.get("/api/plugins/approvals/governance/history", headers=headers)

    assert runtime_pending.status_code == runtime_history.status_code == 200
    assert runtime_pending.json()["profile"] == runtime_history.json()["profile"] == "profile-a"
    assert [item["request_id"] for item in runtime_pending.json()["items"]] == [pending.request_id]
    assert [item["status"] for item in runtime_history.json()["items"]] == ["approved"]
    assert [item["approval_id"] for item in governance_pending.json()["items"]] == ["fixture-pending"]
    assert [item["approval_id"] for item in governance_history.json()["items"]] == ["fixture-history"]
    assert "FIXTURE-SECRET" not in governance_pending.text
    assert "fixture-private-session" not in governance_pending.text
    assert "private_extension" not in governance_pending.text

    original_ledger = ledger.read_text(encoding="utf-8")
    # The dashboard-mounted router has no mutation endpoints, and the runtime
    # and governance fixtures remain unchanged after attempted POST requests.
    assert client.post(
        f"/api/plugins/approvals/{pending.request_id}/respond",
        headers=headers,
        json={"decision": "approve", "expected_version": pending.version},
    ).status_code == 404
    assert client.post(
        "/api/plugins/approvals/governance/fixture-pending/respond",
        headers=headers,
        json={"decision": "approve"},
    ).status_code == 404
    assert [item.request_id for item in store.list_pending() if item.request_id == pending.request_id] == [pending.request_id]
    assert ledger.read_text(encoding="utf-8") == original_ledger


def test_dashboard_adapter_respects_runtime_profile_store_scope(tmp_path, monkeypatch):
    import importlib.util
    import sys
    import types
    from pathlib import Path

    from hermes_approval.store import ApprovalStore

    profile_home = tmp_path / "profile-home"
    profile_home.mkdir()
    active = {"name": "alpha"}
    hermes_cli = types.ModuleType("hermes_cli")
    web_server = types.ModuleType("hermes_cli.web_server")
    web_server._has_valid_session_token = lambda connection: connection.headers.get("X-Test-Auth") == TOKEN
    web_server._has_valid_query_token = lambda connection, path: False
    hermes_cli.web_server = web_server
    profiles = types.ModuleType("hermes_cli.profiles")
    profiles.get_active_profile_name = lambda: active["name"]
    constants = types.ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: profile_home
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.web_server", web_server)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", profiles)
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)

    adapter_path = Path(__file__).parents[1] / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("source_dashboard_plugin_api", adapter_path)
    adapter = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(adapter)
    adapter._store_cache.clear()
    alpha = ApprovalStore(profile_home / "approvals.db", profile="alpha")
    alpha.create(session_id="alpha-session", explanation="alpha-only", source="test")
    alpha_resolved = alpha.create(session_id="alpha-session", explanation="alpha-history", source="test")
    alpha.respond(alpha_resolved.request_id, "approve", expected_version=1)
    beta = ApprovalStore(profile_home / "approvals.db", profile="beta")
    beta.create(session_id="beta-session", explanation="beta-only", source="test")
    beta_resolved = beta.create(session_id="beta-session", explanation="beta-history", source="test")
    beta.respond(beta_resolved.request_id, "deny", expected_version=1)

    app = FastAPI()
    app.include_router(adapter.router, prefix="/api/plugins/approvals")
    client = TestClient(app)
    headers = {"X-Test-Auth": TOKEN}
    first = client.get("/api/plugins/approvals/pending", headers=headers)
    first_history = client.get("/api/plugins/approvals/history", headers=headers)
    active["name"] = "beta"
    second = client.get("/api/plugins/approvals/pending", headers=headers)
    second_history = client.get("/api/plugins/approvals/history", headers=headers)

    assert first.json()["profile"] == first_history.json()["profile"] == "alpha"
    assert [row["explanation"] for row in first.json()["items"]] == ["alpha-only"]
    assert [row["explanation"] for row in first_history.json()["items"]] == ["alpha-history"]
    assert second.json()["profile"] == second_history.json()["profile"] == "beta"
    assert [row["explanation"] for row in second.json()["items"]] == ["beta-only"]
    assert [row["explanation"] for row in second_history.json()["items"]] == ["beta-history"]
    assert "alpha-only" not in second.text and "alpha-history" not in second_history.text
    assert "beta-only" not in first.text and "beta-history" not in first_history.text
