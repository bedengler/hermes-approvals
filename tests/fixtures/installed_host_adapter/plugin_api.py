"""Repository fixture for the installed Hermes dashboard host adapter.

This mirrors the installed adapter's auth and router wiring so the regression
contract can run without depending on a Hermes installation on the runner.
"""
from __future__ import annotations

from pathlib import Path

from starlette.requests import HTTPConnection

from hermes_approval.api import create_router
from hermes_approval.governance import GovernanceApprovalStore, GovernanceDecisionService
from hermes_approval.store import ApprovalStore


def _canonical_governance_decide(approval_id, decision, note):
    """Call the existing governance decision function, never a shell command."""
    import importlib.util

    path = Path.home() / ".hermes/company/integration/governance_mcp/approvals.py"
    spec = importlib.util.spec_from_file_location("hermes_governance_approvals", path)
    if not spec or not spec.loader:
        raise RuntimeError("canonical governance approval module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.decide(approval_id, decision, note)


def _authorize(connection: HTTPConnection) -> bool:
    """Reuse the verdict already established by Hermes dashboard auth."""
    from hermes_cli import web_server

    app = connection.app
    if getattr(app.state, "auth_required", False):
        allowed = bool(
            getattr(connection.state, "token_authenticated", False)
            or getattr(connection.state, "session", None)
        )
    else:
        allowed = bool(
            web_server._has_valid_session_token(connection)
            or web_server._has_valid_query_token(connection, connection.url.path)
        )
    return allowed


from hermes_constants import get_hermes_home
from hermes_cli.profiles import get_active_profile_name

_profile_home = get_hermes_home()
_store = ApprovalStore(
    _profile_home / "approvals.db",
    profile=get_active_profile_name(),
)
_governance_store = GovernanceApprovalStore(
    Path(__import__("os").environ.get("HERMES_GOVERNANCE_HOME", str(Path.home() / ".hermes-governance")))
    / "approvals.jsonl"
)
router = create_router(
    _store,
    authorize=_authorize,
    governance_store=_governance_store,
    governance_decision_service=GovernanceDecisionService(
        _governance_store, _canonical_governance_decide
    ),
)
