"""Regression test for the installed Hermes dashboard host adapter auth seam."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ADAPTER = Path.home() / ".hermes/plugins/approvals/dashboard/plugin_api.py"


def _load_adapter(monkeypatch, *, token: str):
    web_server = types.ModuleType("hermes_cli.web_server")
    web_server._has_valid_session_token = lambda connection: connection.headers.get(
        "X-Hermes-Session-Token"
    ) == token
    web_server._has_valid_query_token = lambda connection, path: False
    hermes_cli = types.ModuleType("hermes_cli")
    hermes_cli.web_server = web_server
    profiles = types.ModuleType("hermes_cli.profiles")
    profiles.get_active_profile_name = lambda: "test"
    constants = types.ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: Path("/tmp/hermes-approval-api-test")
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.web_server", web_server)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", profiles)
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)

    spec = importlib.util.spec_from_file_location("installed_approvals_plugin", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_installed_adapter_authenticates_loopback_header_and_denies_missing(monkeypatch, tmp_path):
    token = "synthetic-dashboard-token"
    module = _load_adapter(monkeypatch, token=token)
    from hermes_approval.store import ApprovalStore

    module._store = ApprovalStore(tmp_path / "approvals.db", profile="test")
    module.router = module.create_router(module._store, authorize=module._authorize)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/approvals")
    client = TestClient(app)

    assert client.get("/api/plugins/approvals/pending").status_code == 403
    response = client.get(
        "/api/plugins/approvals/pending",
        headers={"X-Hermes-Session-Token": token},
    )
    assert response.status_code == 200
    assert response.json()["profile"] == "test"
