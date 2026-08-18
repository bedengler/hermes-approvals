from pathlib import Path

import pytest

from hermes_approval.store import ApprovalConflict, ApprovalStore


def test_create_redacts_sensitive_explanation_and_persists(tmp_path: Path):
    store = ApprovalStore(tmp_path / "approvals.db", profile="work")
    approval = store.create(
        session_id="s1", explanation="Run command with token=super-secret", source="discord"
    )
    assert approval.request_id
    assert "super-secret" not in approval.explanation
    assert approval.explanation == "Run command with token=[REDACTED]"
    reopened = ApprovalStore(tmp_path / "approvals.db", profile="work")
    assert reopened.list_pending()[0].request_id == approval.request_id


def test_respond_requires_version_and_rejects_stale_or_double_action(tmp_path: Path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    approval = store.create(session_id="s", explanation="safe", source="discord")
    resolved = store.respond(approval.request_id, "approve", expected_version=approval.version)
    assert resolved.status == "approved"
    with pytest.raises(ApprovalConflict):
        store.respond(approval.request_id, "deny", expected_version=approval.version)
    assert store.list_pending() == []
    assert store.history()[0].request_id == approval.request_id


def test_legacy_session_resolution_is_oldest_pending_only(tmp_path: Path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    first = store.create(session_id="s", explanation="first", source="discord")
    second = store.create(session_id="s", explanation="second", source="discord")
    result = store.resolve_legacy_session("s", "deny")
    assert result.request_id == first.request_id
    assert store.list_pending()[0].request_id == second.request_id


def test_events_include_created_and_resolved(tmp_path: Path):
    store = ApprovalStore(tmp_path / "a.db", profile="p")
    a = store.create(session_id="s", explanation="safe", source="dashboard")
    store.respond(a.request_id, "deny", expected_version=a.version)
    assert [event["type"] for event in store.events()] == ["approval.created", "approval.resolved"]
