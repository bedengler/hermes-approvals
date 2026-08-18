import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_approval.api import create_router
from hermes_approval.store import ApprovalStore


def test_api_lists_and_responds_by_opaque_id(tmp_path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation="approve safe command", source="discord")
    app = FastAPI()
    app.include_router(create_router(store, authorize=lambda: True), prefix="/api")
    client = TestClient(app)
    response = client.get("/api/approvals/pending")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["request_id"] == approval.request_id
    result = client.post(
        f"/api/approvals/{approval.request_id}/respond",
        json={"decision": "approve", "expected_version": approval.version},
    )
    assert result.status_code == 200
    assert result.json()["status"] == "approved"


def test_api_rejects_bad_expected_version(tmp_path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation="safe", source="discord")
    app = FastAPI()
    app.include_router(create_router(store, authorize=lambda: True), prefix="/api")
    response = TestClient(app).post(
        f"/api/approvals/{approval.request_id}/respond",
        json={"decision": "deny", "expected_version": 99},
    )
    assert response.status_code == 409


def test_api_authorizer_is_called(tmp_path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    app = FastAPI()
    app.include_router(create_router(store, authorize=lambda: False), prefix="/api")
    assert TestClient(app).get("/api/approvals/pending").status_code == 403
