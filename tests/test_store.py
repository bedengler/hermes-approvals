import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_approval.store import (
    MAX_RETENTION_DAYS,
    MAX_RETENTION_EVENTS,
    ApprovalConflict,
    ApprovalNotFound,
    ApprovalStore,
    redact,
)


def test_redaction_covers_command_and_key_value_forms_before_persistence(tmp_path: Path):
    text = "run --token super-secret --password=hunter2 --api-key 'quoted secret' --foo=value Bearer abc123 token=also-secret"
    store = ApprovalStore(tmp_path / "approvals.db", profile="work")
    approval = store.create(session_id="s1", explanation=text, source="tui")
    reopened = ApprovalStore(tmp_path / "approvals.db", profile="work")
    persisted = reopened.list_pending()[0].explanation
    assert all(secret not in persisted for secret in ("super-secret", "hunter2", "quoted secret", "value", "abc123", "also-secret"))
    assert persisted == redact(text)
    assert len(persisted) <= 2000


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("token", "json-token-secret"),
        ("password", "json-password-secret"),
        ("passwd", "json-passwd-secret"),
        ("secret", "json-secret-value"),
        ("api_key", "json-api-key-secret"),
        ("api-key", "json-api-key-dashed-secret"),
        ("access_key", "json-access-key-secret"),
        ("access-key", "json-access-key-dashed-secret"),
        ("client_id", "json-client-id-secret"),
        ("client-id", "json-client-id-dashed-secret"),
        ("client_secret", "json-client-secret-secret"),
        ("client-secret", "json-client-secret-dashed-secret"),
        ("private_key", "json-private-key-secret"),
        ("private-key", "json-private-key-dashed-secret"),
    ],
)
@pytest.mark.parametrize("quoted_value", [True, False])
def test_redaction_covers_quoted_json_style_credential_keys_and_values(
    key: str, value: str, quoted_value: bool,
):
    rendered_value = f'"{value} with spaces"' if quoted_value else value
    text = f'{{"{key}":{rendered_value},"ordinary":"preserved"}}'

    redacted = redact(text)

    assert value not in redacted
    assert '"ordinary":"preserved"' in redacted
    assert f'"{key}":' in redacted


@pytest.mark.parametrize(
    "text",
    [
        json.dumps({"token": 'prefix\\"LEAK', "ordinary": "preserved"}),
        json.dumps({"password": r"prefix\LEAK", "ordinary": "preserved"}),
        r'--token "prefix\"LEAK" trailing=value',
        r"--password 'prefix\'LEAK' trailing=value",
        r'Bearer "prefix\"LEAK" trailing=value',
        r"Basic 'prefix\'LEAK' trailing=value",
    ],
)
def test_redaction_consumes_escaped_quotes_and_backslashes_in_quoted_values(text: str):
    redacted = redact(text)

    assert "LEAK" not in redacted
    assert "trailing=value" in redacted or "ordinary" in redacted


@pytest.mark.parametrize(
    "key",
    [
        "auth", "authentication", "credential",
        "auth_key", "auth-key", "authentication_key", "authentication-key",
        "credential_key", "credential-key",
    ],
)
@pytest.mark.parametrize("key_quote", ["", '"', "'"])
@pytest.mark.parametrize("separator", ["=", ":"])
@pytest.mark.parametrize("value_quote", ["", '"', "'"])
def test_redaction_covers_ordinary_authentication_key_value_forms(
    key: str, key_quote: str, separator: str, value_quote: str,
):
    value = f"{key}-ordinary-secret"
    text = f"{key_quote}{key}{key_quote}{separator} {value_quote}{value}{value_quote}"

    redacted = redact(text)

    assert value not in redacted


def test_redaction_covers_client_credentials_private_keys_and_basic_auth(tmp_path: Path):
    text = (
        "client_secret=secret-one client-secret: secret-two client_id=client-three "
        "client-id=client-four private_key=key-one private-key: key-two "
        "run --client-secret secret-five --private-key=key-three "
        "Authorization: Basic dXNlcjpwYXNz"
    )
    store = ApprovalStore(tmp_path / "approvals.db", profile="work")
    approval = store.create(session_id="s1", explanation=text, source="client_secret=source-secret")
    persisted = ApprovalStore(tmp_path / "approvals.db", profile="work").list_pending()[0]
    secrets = (
        "secret-one", "secret-two", "client-three", "client-four", "key-one",
        "key-two", "secret-five", "key-three", "dXNlcjpwYXNz", "source-secret",
    )
    assert all(secret not in persisted.explanation for secret in secrets[:-1])
    assert "source-secret" not in persisted.source
    assert approval.explanation == persisted.explanation


@pytest.mark.parametrize(
    ("scheme", "credential"),
    [("Bearer", "quoted bearer credential"), ("Basic", "quoted basic credential")],
)
def test_redaction_consumes_quoted_bearer_and_basic_values_with_spaces(
    tmp_path: Path, scheme: str, credential: str,
):
    text = f'Authorization: {scheme} "{credential}" trailing=value'
    store = ApprovalStore(tmp_path / "approvals.db", profile="work")
    approval = store.create(session_id="s1", explanation=text, source=text)
    persisted = ApprovalStore(tmp_path / "approvals.db", profile="work").list_pending()[0]

    assert credential not in approval.explanation
    assert credential not in approval.source
    assert credential not in persisted.explanation
    assert credential not in persisted.source
    assert credential not in str(ApprovalStore(tmp_path / "approvals.db", profile="work").events())


def test_redaction_consumes_multiline_private_key_through_matching_end_marker():
    pem_material = "line-one-secret\nline-two-secret"
    begin = "-----" + "BEGIN PRIVATE KEY" + "-----"
    end = "-----" + "END PRIVATE KEY" + "-----"
    text = (
        'token="quoted token with spaces" password=\'quoted password with spaces\' '
        'api-key="quoted api key" client_secret="quoted client secret" '
        f"private_key={begin}\n{pem_material}\n{end}\nordinary=value"
    )
    redacted = redact(text)
    assert "quoted token with spaces" not in redacted
    assert "quoted password with spaces" not in redacted
    assert "quoted api key" not in redacted
    assert "quoted client secret" not in redacted
    assert pem_material not in redacted
    assert "-----BEGIN PRIVATE KEY-----" not in redacted
    assert begin not in redacted
    assert end not in redacted
    assert "ordinary=value" in redacted


@pytest.mark.parametrize(
    "text",
    [
        "-----" + "BEGIN PRIVATE KEY" + "-----\nUNTERMINATED-PEM-SECRET-987\nordinary",
        "-----" + "BEGIN PRIVATE KEY" + "-----\nMISMATCHED-PEM-SECRET-987\n" + "-----" + "END RSA PRIVATE KEY" + "-----\nordinary",
        "-----" + "BEGIN PRIVATE KEY" + "-----\n" + "x" * 2000 + "TRUNCATED-PEM-SECRET-987",
    ],
)
def test_redaction_fails_closed_for_malformed_or_truncated_private_keys(text):
    redacted = redact(text)

    assert "PEM-SECRET-987" not in redacted
    assert len(redacted) <= 2000


@pytest.mark.parametrize(
    "text",
    [
        "-----" + "BEGIN PRIVATE KEY" + "-----\nUNTERMINATED-PEM-SECRET-987\nordinary",
        "-----" + "BEGIN PRIVATE KEY" + "-----\nMISMATCHED-PEM-SECRET-987\n" + "-----" + "END RSA PRIVATE KEY" + "-----\nordinary",
        "-----" + "BEGIN PRIVATE KEY" + "-----\n" + "x" * 2000 + "TRUNCATED-PEM-SECRET-987",
    ],
)
def test_malformed_or_truncated_private_keys_never_reach_sqlite_or_events(tmp_path: Path, text: str):
    store = ApprovalStore(tmp_path / "approvals.db", profile="p")
    approval = store.create(session_id="s", explanation=text, source=text)
    store.respond(approval.request_id, "deny", expected_version=1)

    with store._connect() as db:
        rows = db.execute("SELECT explanation, source FROM approvals").fetchone()
        events = [row["payload"] for row in db.execute("SELECT payload FROM approval_events")]
    assert "PEM-SECRET-987" not in str(tuple(rows))
    assert all("PEM-SECRET-987" not in payload for payload in events)


def test_credential_values_never_appear_in_persistence_or_event_payloads(tmp_path: Path):
    secret = "persist-me-not"
    store = ApprovalStore(tmp_path / "approvals.db", profile="p")
    approval = store.create(session_id="s", explanation=f"client_secret={secret}", source="tui")
    store.respond(approval.request_id, "deny", expected_version=1)
    with store._connect() as db:
        raw = db.execute("SELECT explanation, source FROM approvals").fetchone()
        events = [row["payload"] for row in db.execute("SELECT payload FROM approval_events")]
    assert secret not in str(tuple(raw))
    assert all(secret not in payload for payload in events)


def test_dtos_never_need_internal_session_id_in_event_payload(tmp_path: Path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="private", explanation="safe", source="discord")
    store.respond(approval.request_id, "deny", expected_version=1)
    assert all("session_id" not in event["payload"] for event in store.events())


def test_respond_requires_version_and_rejects_stale_or_double_action(tmp_path: Path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation="safe", source="discord")
    resolved = store.respond(approval.request_id, "approve", expected_version=approval.version)
    assert resolved.status == "approved"
    with pytest.raises(ApprovalConflict):
        store.respond(approval.request_id, "deny", expected_version=approval.version)
    assert store.list_pending() == []
    assert store.history()[0].request_id == approval.request_id


def test_legacy_session_resolution_is_oldest_pending_only_and_survives_restart(tmp_path: Path):
    path = tmp_path / "a.db"
    store = ApprovalStore(path, profile="p")
    first = store.create(session_id="s", explanation="first", source="discord")
    second = store.create(session_id="s", explanation="second", source="discord")
    result = store.resolve_legacy_session("s", "deny")
    assert result.request_id == first.request_id
    reopened = ApprovalStore(path, profile="p")
    assert reopened.list_pending()[0].request_id == second.request_id
    assert [event["type"] for event in reopened.events()] == ["approval.created", "approval.created", "approval.resolved"]


def test_legacy_session_resolution_finds_matching_session_beyond_first_page(tmp_path: Path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    for index in range(501):
        store.create(session_id=f"other-{index}", explanation="other", source="test")
    first = store.create(session_id="target", explanation="first target", source="test")
    second = store.create(session_id="target", explanation="second target", source="test")

    result = store.resolve_legacy_session("target", "deny")

    assert result.request_id == first.request_id
    assert result.status == "denied"
    with store._connect() as db:
        remaining = db.execute(
            "SELECT status FROM approvals WHERE request_id=? AND profile=?",
            (second.request_id, "p"),
        ).fetchone()
    assert remaining["status"] == "pending"


def test_event_and_history_limits_are_positive_and_bounded(tmp_path: Path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation="safe", source="dashboard")
    with pytest.raises(ValueError):
        store.history(limit=0)
    with pytest.raises(ValueError):
        store.events(limit=-1)
    assert len(store.events(limit=1)) == 1
    assert len(store.events(limit=500)) == 1
    store.respond(approval.request_id, "deny", expected_version=1)
    assert len(store.events(after_id=0, limit=1)) == 1


def test_respond_update_and_event_rollback_together(tmp_path: Path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation="safe", source="dashboard")
    original = store._event
    store._event = lambda *args: (_ for _ in ()).throw(RuntimeError("injected event failure"))
    with pytest.raises(RuntimeError):
        store.respond(approval.request_id, "deny", expected_version=1)
    store._event = original
    assert store.list_pending()[0].status == "pending"
    assert [event["type"] for event in store.events()] == ["approval.created"]


def test_concurrent_responders_resolve_once(tmp_path: Path):
    path = tmp_path / "a.db"
    approval = ApprovalStore(path, profile="p").create(session_id="s", explanation="safe", source="tui")

    def attempt(decision):
        try:
            return ApprovalStore(path, profile="p").respond(approval.request_id, decision, expected_version=1).status
        except ApprovalConflict:
            return "conflict"

    decisions = ("approve", "deny")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, decisions))

    outcomes = dict(zip(decisions, results))
    winners = [(decision, status) for decision, status in outcomes.items() if status != "conflict"]
    assert len(winners) == 1
    assert len([status for status in outcomes.values() if status == "conflict"]) == 1
    winning_decision, winning_status = winners[0]
    assert (winning_decision, winning_status) in {
        ("approve", "approved"),
        ("deny", "denied"),
    }

    persisted = ApprovalStore(path, profile="p").history()[0]
    assert persisted.status == winning_status
    assert persisted.decision == winning_decision
    events = ApprovalStore(path, profile="p").events()
    assert [e["type"] for e in events].count("approval.resolved") == 1


def test_profile_boundary_and_cleanup(tmp_path: Path):
    path = tmp_path / "a.db"
    first = ApprovalStore(path, profile="one").create(session_id="s", explanation="safe", source="tui")
    other = ApprovalStore(path, profile="two")
    assert other.list_pending() == []
    with pytest.raises(ApprovalNotFound):
        other.respond(first.request_id, "approve", expected_version=1)
    with pytest.raises(ApprovalNotFound):
        other.resolve_legacy_session("s", "approve")
    store = ApprovalStore(path, profile="one")
    store.respond(first.request_id, "approve", expected_version=1)
    assert store.cleanup(max_events=1) == 1
    assert len(store.events(limit=500)) == 1


def test_cleanup_deletes_resolved_approvals_by_age_but_events_only_by_max_events(tmp_path: Path):
    path = tmp_path / "a.db"
    store = ApprovalStore(path, profile="p")
    approval = store.create(session_id="s", explanation="safe", source="tui")
    store.respond(approval.request_id, "approve", expected_version=1)
    with store._connect() as db:
        db.execute("UPDATE approvals SET resolved_at=? WHERE request_id=?", (1.0, approval.request_id))
        db.execute("UPDATE approval_events SET created_at=?", (1.0,))
    assert store.cleanup(retention_days=1, max_events=10) == 0
    assert store.history() == []
    assert len(store.events(limit=500)) == 2


@pytest.mark.parametrize("value", [0, -1, True, False, 1.0, "1"])
def test_cleanup_rejects_invalid_retention_days(tmp_path: Path, value):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    with pytest.raises(ValueError):
        store.cleanup(retention_days=value)


@pytest.mark.parametrize("value", [0, -1, True, False, 1.0, "1"])
def test_cleanup_rejects_invalid_max_events(tmp_path: Path, value):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    with pytest.raises(ValueError):
        store.cleanup(max_events=value)


def test_cleanup_accepts_documented_retention_boundaries_and_rejects_overages(tmp_path: Path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    assert store.cleanup(retention_days=1, max_events=1) == 0
    assert store.cleanup(retention_days=MAX_RETENTION_DAYS, max_events=MAX_RETENTION_EVENTS) == 0
    with pytest.raises(ValueError):
        store.cleanup(retention_days=MAX_RETENTION_DAYS + 1)
    with pytest.raises(ValueError):
        store.cleanup(max_events=MAX_RETENTION_EVENTS + 1)
