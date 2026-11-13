"""Hermes dashboard adapter and reusable router factory.

The supported Hermes dashboard host imports this module's ``router`` from the
manifest's ``api`` field. Its store is bound to the active profile home, and
authorization delegates to the host's canonical dashboard checks.
"""
from os import environ
from pathlib import Path
from threading import RLock

from hermes_approval.api import create_router
from hermes_approval.governance import GovernanceApprovalStore
from hermes_approval.store import ApprovalStore


def build_router(store, dashboard_authorize, governance_path=None):
    if governance_path is None:
        governance_home = Path(
            environ.get("HERMES_GOVERNANCE_HOME", str(Path.home() / ".hermes-governance"))
        ).expanduser()
        governance_path = governance_home / "approvals.jsonl"
    governance = GovernanceApprovalStore(governance_path)
    return create_router(
        store,
        authorize=dashboard_authorize,
        governance_store=governance,
        allow_decisions=False,
    )


def _authorize(connection):
    """Reuse Hermes dashboard's canonical request-authentication verdict."""
    from hermes_cli import web_server

    if getattr(connection.app.state, "auth_required", False):
        return bool(
            getattr(connection.state, "token_authenticated", False)
            or getattr(connection.state, "session", None)
        )
    return bool(
        web_server._has_valid_session_token(connection)
        or web_server._has_valid_query_token(connection, connection.url.path)
    )


_store_cache = {}
_store_cache_lock = RLock()


def _active_profile_store():
    """Return the store for the Hermes home/profile active in this request."""
    from hermes_cli.profiles import get_active_profile_name
    from hermes_constants import get_hermes_home

    profile_home = Path(get_hermes_home())
    profile_name = get_active_profile_name() or "default"
    key = (str(profile_home.resolve()), profile_name)
    with _store_cache_lock:
        store = _store_cache.get(key)
        if store is None:
            store = _store_cache[key] = ApprovalStore(
                profile_home / "approvals.db", profile=profile_name
            )
        return store


class _ActiveProfileStore:
    """Delegate each store operation to the profile scoped by Hermes per request."""

    def __getattr__(self, name):
        return getattr(_active_profile_store(), name)


# Hermes imports this module by its manifest-declared API path. The provider
# resolves the profile at request time so Hermes' profile-scope dependency can
# select the proper database for `?profile=` requests. Package-only callers can
# still use build_router() with explicitly injected dependencies.
_store = _ActiveProfileStore()
router = build_router(_store, _authorize)
