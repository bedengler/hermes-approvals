import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_approval.api import create_router
from hermes_approval.store import ApprovalStore


def make_app(store, authorized=True, prefix="/api/plugins/approvals"):
    app = FastAPI()
    app.include_router(create_router(store, authorize=lambda _connection: authorized), prefix=prefix)
    return app


def test_api_uses_explicit_dtos_without_session_id(tmp_path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="private-session", explanation="safe", source="discord")
    client = TestClient(make_app(store))
    item = client.get("/api/plugins/approvals/pending").json()["items"][0]
    assert item["request_id"] == approval.request_id
    assert "session_id" not in item
    resolved = client.post(
        f"/api/plugins/approvals/{approval.request_id}/respond",
        json={"decision": "approve", "expected_version": approval.version},
    )
    assert resolved.status_code == 200
    assert "session_id" not in resolved.json()
    assert "session_id" not in client.get("/api/plugins/approvals/history").json()["items"][0]
    event = client.get("/api/plugins/approvals/events").json()["events"][0]
    assert "session_id" not in event
    assert "session_id" not in event["payload"]


def test_http_event_dto_recursively_sanitizes_internal_session_id(tmp_path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    forbidden = {
        "id": 1,
        "profile": "p",
        "request_id": "r",
        "type": "test",
        "payload": {"safe": {"value": 1, "session_id": "private"}, "items": [{"session_id": "nested"}]},
        "created_at": 1.0,
    }
    store.events = lambda *, after_id=0, limit=100: [forbidden]
    response = TestClient(make_app(store)).get("/api/plugins/approvals/events")
    assert response.status_code == 200
    payload = response.json()["events"][0]["payload"]
    assert payload == {"safe": {"value": 1}, "items": [{}]}
    assert "session_id" not in response.text


def test_credential_values_are_absent_from_http_websocket_and_event_payloads(tmp_path):
    secret = "http-ws-secret"
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation=f'{{"client_secret":"{secret}"}}', source=f'{{"token":{secret}}}')
    client = TestClient(make_app(store))
    pending = client.get("/api/plugins/approvals/pending")
    assert pending.status_code == 200
    assert secret not in pending.text
    store.respond(approval.request_id, "deny", expected_version=1)
    event_response = client.get("/api/plugins/approvals/events")
    assert event_response.status_code == 200
    assert secret not in event_response.text
    with client.websocket_connect("/api/plugins/approvals/events/stream") as websocket:
        event = websocket.receive_json()
    assert secret not in str(event)


@pytest.mark.parametrize("form", ["json", "command", "bearer", "basic"])
def test_escaped_quoted_credentials_are_absent_from_all_api_surfaces(tmp_path, form):
    if form == "json":
        explanation = json.dumps({"token": 'prefix\\"LEAK', "ordinary": "preserved"})
    elif form == "command":
        explanation = r'--token "prefix\"LEAK" trailing=value'
    elif form == "bearer":
        explanation = r'Bearer "prefix\"LEAK" trailing=value'
    else:
        explanation = r"Basic 'prefix\'LEAK' trailing=value"

    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation=explanation, source=explanation)
    client = TestClient(make_app(store))

    assert "LEAK" not in client.get("/api/plugins/approvals/pending").text
    with store._connect() as db:
        persisted = db.execute("SELECT explanation, source FROM approvals").fetchone()
    assert "LEAK" not in str(tuple(persisted))

    store.respond(approval.request_id, "deny", expected_version=1)
    assert "LEAK" not in client.get("/api/plugins/approvals/events").text
    with client.websocket_connect("/api/plugins/approvals/events/stream") as websocket:
        assert "LEAK" not in str(websocket.receive_json())


@pytest.mark.parametrize("key", ["auth", "authentication", "credential"])
@pytest.mark.parametrize("separator", ["=", ":"])
@pytest.mark.parametrize("key_quote", ["", '"', "'"])
@pytest.mark.parametrize("value_quote", ["", '"', "'"])
def test_ordinary_authentication_key_values_are_absent_from_sqlite_http_and_events(
    tmp_path, key, separator, key_quote, value_quote,
):
    secret = f"{key}-http-ws-secret"
    rendered = f"{key_quote}{key}{key_quote}{separator} {value_quote}{secret}{value_quote}"
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation=rendered, source=rendered)
    client = TestClient(make_app(store))

    assert secret not in client.get("/api/plugins/approvals/pending").text
    with store._connect() as db:
        persisted = db.execute("SELECT explanation, source FROM approvals WHERE request_id=?", (approval.request_id,)).fetchone()
    assert secret not in str(tuple(persisted))

    store.respond(approval.request_id, "deny", expected_version=1)
    assert secret not in client.get("/api/plugins/approvals/events").text
    with client.websocket_connect("/api/plugins/approvals/events/stream") as websocket:
        assert secret not in str(websocket.receive_json())


@pytest.mark.parametrize("scheme", ["Bearer", "Basic"])
def test_quoted_bearer_and_basic_credentials_are_absent_from_api_and_events(tmp_path, scheme):
    credential = f"{scheme.lower()} quoted credential with spaces"
    explanation = f'Authorization: {scheme} "{credential}"'
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation=explanation, source=explanation)
    client = TestClient(make_app(store))

    assert credential not in client.get("/api/plugins/approvals/pending").text
    with store._connect() as db:
        persisted = db.execute("SELECT explanation, source FROM approvals").fetchone()
    assert credential not in str(tuple(persisted))

    store.respond(approval.request_id, "deny", expected_version=1)
    assert credential not in client.get("/api/plugins/approvals/events").text
    with client.websocket_connect("/api/plugins/approvals/events/stream") as websocket:
        assert credential not in str(websocket.receive_json())


def test_quoted_credentials_and_multiline_private_key_are_absent_from_all_api_surfaces(tmp_path):
    quoted = "quoted" + "-credential-value"
    begin = "-----" + "BEGIN PRIVATE KEY" + "-----"
    end = "-----" + "END PRIVATE KEY" + "-----"
    pem_line = "pem-material-line"
    explanation = (
        f'client-id="{quoted} with spaces" '
        f'private_key={begin}\n{pem_line}\n{end}'
    )
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation=explanation, source="tui")
    client = TestClient(make_app(store))

    pending = client.get("/api/plugins/approvals/pending")
    assert pending.status_code == 200
    assert quoted not in pending.text
    assert pem_line not in pending.text
    with store._connect() as db:
        persisted = db.execute("SELECT explanation FROM approvals").fetchone()["explanation"]
    assert quoted not in persisted
    assert pem_line not in persisted

    store.respond(approval.request_id, "deny", expected_version=1)
    events = client.get("/api/plugins/approvals/events")
    assert quoted not in events.text
    with client.websocket_connect("/api/plugins/approvals/events/stream") as websocket:
        assert quoted not in str(websocket.receive_json())


@pytest.mark.parametrize(
    "text",
    [
        "-----" + "BEGIN PRIVATE KEY" + "-----\nUNTERMINATED-PEM-SECRET-987\nordinary",
        "-----" + "BEGIN PRIVATE KEY" + "-----\nMISMATCHED-PEM-SECRET-987\n" + "-----" + "END RSA PRIVATE KEY" + "-----\nordinary",
        "-----" + "BEGIN PRIVATE KEY" + "-----\n" + "x" * 2000 + "TRUNCATED-PEM-SECRET-987",
    ],
)
def test_malformed_or_truncated_private_keys_are_absent_from_http_websocket_and_events(tmp_path, text):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation=text, source=text)
    client = TestClient(make_app(store))

    assert "PEM-SECRET-987" not in client.get("/api/plugins/approvals/pending").text
    with store._connect() as db:
        persisted = db.execute("SELECT explanation, source FROM approvals").fetchone()
    assert "PEM-SECRET-987" not in str(tuple(persisted))

    store.respond(approval.request_id, "deny", expected_version=1)
    assert "PEM-SECRET-987" not in client.get("/api/plugins/approvals/events").text
    with client.websocket_connect("/api/plugins/approvals/events/stream") as websocket:
        assert "PEM-SECRET-987" not in str(websocket.receive_json())


def test_websocket_event_dto_recursively_sanitizes_internal_session_id(tmp_path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    forbidden = {
        "id": 1,
        "profile": "p",
        "request_id": "r",
        "type": "test",
        "payload": {"nested": [{"session_id": "private", "safe": True}]},
        "created_at": 1.0,
    }
    store.events = lambda *, after_id=0, limit=100: [forbidden]
    with TestClient(make_app(store)).websocket_connect("/api/plugins/approvals/events/stream") as websocket:
        event = websocket.receive_json()
    assert event["payload"] == {"nested": [{"safe": True}]}
    assert "session_id" not in str(event)


def test_api_authorization_denies_http_and_websocket(tmp_path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    client = TestClient(make_app(store, authorized=False))
    assert client.get("/api/plugins/approvals/pending").status_code == 403
    with pytest.raises(Exception):
        with client.websocket_connect("/api/plugins/approvals/events/stream"):
            pass


def test_api_rejects_bad_decision_version_and_limits(tmp_path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation="safe", source="discord")
    client = TestClient(make_app(store))
    assert client.post(f"/api/plugins/approvals/{approval.request_id}/respond", json={"decision": "maybe", "expected_version": 1}).status_code == 422
    assert client.post(f"/api/plugins/approvals/{approval.request_id}/respond", json={"decision": "deny", "expected_version": 0}).status_code == 422
    assert client.get("/api/plugins/approvals/history?limit=0").status_code == 422
    assert client.get("/api/plugins/approvals/events?limit=-1").status_code == 422
    assert client.get("/api/plugins/approvals/events?after_id=-1").status_code == 422


def test_host_adapter_mounts_routes_and_callback_controls_access(tmp_path):
    from dashboard.plugin_api import build_router

    store = ApprovalStore(tmp_path / "a.db", profile="p")
    app = FastAPI()
    app.include_router(build_router(store, lambda _connection: True), prefix="/api/plugins/approvals")
    client = TestClient(app)
    assert client.get("/api/plugins/approvals/pending").status_code == 200
    assert client.get("/api/plugins/approvals/pending").json()["profile"] == "p"


def test_governance_is_read_only_redacted_expiry_and_auth_scoped(tmp_path):
    from datetime import datetime, timedelta, timezone
    from hermes_approval.governance import GovernanceApprovalStore

    now = datetime.now(timezone.utc)
    ledger = tmp_path / "approvals.jsonl"
    rows = [
        {"approval_id": "apr_live", "created_at": now.isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(), "gate": "production", "action": "restart", "target": "host", "rationale": "Bearer TOPSECRET", "status": "pending", "decision_note": None},
        {"approval_id": "apr_old", "created_at": now.isoformat(), "expires_at": (now - timedelta(hours=1)).isoformat(), "gate": "external", "action": "publish", "target": "repo", "rationale": "ordinary", "status": "pending", "decision_note": None},
        {"approval_id": "apr_done", "created_at": now.isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(), "gate": "publish", "action": "publish", "target": "repo", "rationale": "ordinary", "status": "approved", "decision_note": "approved by Bernie"},
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    store = ApprovalStore(tmp_path / "runtime.db", profile="p")
    governance = GovernanceApprovalStore(ledger)
    app = make_app(store, authorized=True)
    app.router.routes.extend(create_router(store, authorize=lambda _connection: True, governance_store=governance).routes)
    client = TestClient(app)
    pending = client.get("/governance/pending")
    assert pending.status_code == 200
    assert [item["approval_id"] for item in pending.json()["items"]] == ["apr_live"]
    assert "TOPSECRET" not in pending.text
    history_statuses = {item["status"] for item in client.get("/governance/history").json()["items"]}
    assert {"approved", "expired"} <= history_statuses
    denied = make_app(store, authorized=False)
    denied.router.routes.extend(create_router(store, authorize=lambda _connection: False, governance_store=governance).routes)
    assert TestClient(denied).get("/governance/pending").status_code == 403
    assert client.post("/governance/apr_live/respond", json={"decision": "approve"}).status_code == 503


def test_governance_decision_exact_id_uses_injected_service_and_updates_fixture(tmp_path):
    from datetime import datetime, timedelta, timezone
    from hermes_approval.governance import GovernanceApprovalStore, GovernanceDecisionService

    now = datetime.now(timezone.utc)
    ledger = tmp_path / "approvals.jsonl"
    rows = [{"approval_id": "apr_a", "created_at": now.isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(), "gate": "g", "action": "a", "target": "t", "rationale": "safe", "status": "pending"},
            {"approval_id": "apr_b", "created_at": now.isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(), "gate": "g", "action": "b", "target": "t", "rationale": "safe", "status": "pending"}]
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    called = []
    def fake_decide(approval_id, decision, note):
        called.append((approval_id, decision, note))
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**next(r for r in rows if r["approval_id"] == approval_id), "status": decision, "decision_note": note}) + "\n")
        return {"approval_id": approval_id, "status": decision}
    store = GovernanceApprovalStore(ledger)
    service = GovernanceDecisionService(store, fake_decide)
    app = make_app(ApprovalStore(tmp_path / "runtime.db", profile="p"))
    app.router.routes.extend(create_router(ApprovalStore(tmp_path / "other.db", profile="p"), authorize=lambda _: True, governance_store=store, governance_decision_service=service).routes)
    client = TestClient(app)
    response = client.post("/governance/apr_b/respond", json={"decision": "deny", "note": "Bearer DECISION-SECRET-987"})
    assert response.status_code == 200
    assert "DECISION-SECRET-987" not in response.text
    assert called == [("apr_b", "denied", "Bearer DECISION-SECRET-987")]
    assert store.get("apr_a")["status"] == "pending"
    assert client.post("/governance/apr_b/respond", json={"decision": "approve"}).status_code == 409
    assert client.post("/governance/unknown/respond", json={"decision": "approve"}).status_code == 404




def test_governance_public_http_and_dashboard_payloads_redact_all_public_fields(tmp_path):
    from datetime import datetime, timedelta, timezone
    from hermes_approval.governance import GovernanceApprovalStore

    now = datetime.now(timezone.utc)
    secret = "GOVERNANCE-SECRET-987"
    pem = "-----BEGIN PRIVATE KEY-----\\nPEM-MATERIAL-987\\n-----END PRIVATE KEY-----"
    ledger = tmp_path / "approvals.jsonl"
    row = {
        "approval_id": "apr_public",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "gate": {"name": "production", "nested": {"session_id": "session-private"}},
        "action": {"command": f'{{"token":"{secret}"}}', "nested": [{"credential": secret}]},
        "target": f'{{"token":"{secret}"}}',
        "rationale": f"private_key={pem}",
        "decision_note": f"Bearer {secret}",
        "status": "pending",
        "session_id": "session-private",
        "private_nested": {"password": secret},
    }
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")
    runtime = ApprovalStore(tmp_path / "runtime.db", profile="p")
    app = FastAPI()
    app.include_router(
        create_router(runtime, authorize=lambda _: True, governance_store=GovernanceApprovalStore(ledger)),
        prefix="/api/plugins/approvals",
    )
    client = TestClient(app)

    response = client.get("/api/plugins/approvals/governance/pending")
    assert response.status_code == 200
    payload = response.json()["items"][0]
    assert set(payload) == {
        "approval_id", "gate", "target", "action", "rationale", "decision_note",
        "status", "created_at", "expires_at", "decided_at",
    }
    assert "session_id" not in response.text
    assert secret not in response.text
    assert "PEM-MATERIAL-987" not in response.text
    # The dashboard receives only this explicit DTO; no ledger/private fields
    # are available to its renderers even when the ledger row is extended.
    assert set(payload) <= {
        "approval_id", "gate", "target", "action", "rationale", "decision_note",
        "status", "created_at", "expires_at", "decided_at",
    }

    history = client.get("/api/plugins/approvals/governance/history")
    assert history.status_code == 200
    assert secret not in history.text
    assert "session_id" not in history.text


def test_governance_decision_rejects_expired_without_call(tmp_path):
    from datetime import datetime, timedelta, timezone
    from hermes_approval.governance import GovernanceApprovalStore, GovernanceDecisionService
    ledger = tmp_path / "approvals.jsonl"
    row = {"approval_id": "apr_expired", "created_at": datetime.now(timezone.utc).isoformat(), "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), "gate": "g", "action": "a", "target": "t", "rationale": "safe", "status": "pending"}
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")
    called = []
    service = GovernanceDecisionService(GovernanceApprovalStore(ledger), lambda *args: called.append(args))
    app = FastAPI()
    app.include_router(create_router(ApprovalStore(tmp_path / "runtime.db", profile="p"), authorize=lambda _: True, governance_store=GovernanceApprovalStore(ledger), governance_decision_service=service))
    response = TestClient(app).post("/governance/apr_expired/respond", json={"decision": "approve"})
    assert response.status_code == 409
    assert called == []
