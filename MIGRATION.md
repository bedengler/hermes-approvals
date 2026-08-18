# Migration and host integration

This repository deliberately stops at a reusable package boundary. It does
not modify `/Users/bedenglerai/.hermes/hermes-agent` and does not automatically
wire the live approval queue.

## Host adapter

1. Install the `hermes-approval-api` wheel in the host plugin environment. The
   wheel includes `dashboard/plugin_api.py`, `dashboard/manifest.json`, and
   `dashboard/dist/index.js`, so installation does not depend on a source
   checkout or a separately copied dashboard directory.
2. Construct `ApprovalStore(profile_home / "approvals.db", profile=active_profile)`.
3. Build routes with `dashboard.plugin_api.build_router(store, dashboard_authorize)`.
4. Mount the returned router at `/api/plugins/approvals`.
5. Install `dashboard/` as the static dashboard payload and use the host SDK's
   authenticated `fetchJSON`/plugin registry APIs.

The included `test_host_adapter_mounts_routes_and_callback_controls_access`
fixture proves the route prefix and injected authorization seam without
inventing Hermes APIs.

## Existing approvals

Keep existing session-based resolution during migration by calling
`resolve_legacy_session(session_id, decision)`. New UI actions must use the
opaque `request_id` plus `expected_version` (and optionally `nonce`). SQLite
state updates and resolution events commit atomically; a failed event insert
rolls back the approval response.

## Restart and retention

The SQLite schema is created idempotently on startup, so reopening the same
profile database preserves pending/history/events. Hosts should schedule
`store.cleanup()` at least daily. The default policy deletes resolved approvals
after 90 days and keeps the newest 10,000 events per profile. `retention_days`
must be an integer from 1 through 3,650, and `max_events` must be an integer
from 1 through 100,000. Booleans, floats, strings, zero, negatives, and values
above those limits are rejected. Events do not have an age-based policy:
`max_events` is their sole retention limit, so an event may outlive its
resolved approval row.
