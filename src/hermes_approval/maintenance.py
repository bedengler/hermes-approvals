"""Hermes cron entry point for profile-scoped approval retention."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .store import DEFAULT_MAX_EVENTS, DEFAULT_RETENTION_DAYS, ApprovalStore


def cleanup_active_profile() -> dict[str, Any]:
    """Apply configured retention in the profile scoped by Hermes' cron runner."""
    from hermes_cli.config import load_config
    from hermes_cli.profiles import get_active_profile_name
    from hermes_constants import get_hermes_home

    home = Path(get_hermes_home())
    profile = get_active_profile_name() or "default"
    config = load_config()
    settings = config.get("approvals", {})
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError("approvals configuration must be a mapping")

    retention_days = settings.get("retention_days", DEFAULT_RETENTION_DAYS)
    max_events = settings.get("max_events", DEFAULT_MAX_EVENTS)
    store = ApprovalStore(home / "approvals.db", profile=profile)
    deleted_events = store.cleanup(retention_days=retention_days, max_events=max_events)
    return {
        "profile": profile,
        "retention_days": retention_days,
        "max_events": max_events,
        "deleted_events": deleted_events,
    }


def main() -> int:
    """Cron-safe CLI: non-zero exit makes Hermes record and alert on failure."""
    try:
        result = cleanup_active_profile()
    except Exception as exc:
        print(f"Approval cleanup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(
        "Approval cleanup completed: "
        f"profile={result['profile']} "
        f"retention_days={result['retention_days']} "
        f"max_events={result['max_events']} "
        f"deleted_events={result['deleted_events']}"
    )
    return 0
