# Migration and host integration

This repository deliberately stops at a reusable package boundary. It does
not modify the Hermes installation or host-managed profiles and does not
automatically wire the live approval queue.

## Host adapter

1. Set the optional Hermes home override before installation when the host
   uses a non-default location:

   ```bash
   export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
   ```

   Installers and host processes should discover this value at installation or
   startup; they must not copy a developer's absolute path into configuration.
2. Install the `hermes-approval-api` wheel in the host plugin environment. The
   wheel includes `dashboard/plugin_api.py`, `dashboard/manifest.json`, and
   `dashboard/dist/index.js`, so installation does not depend on a source
   checkout or a separately copied dashboard directory.
3. At startup, use the host profile API to obtain the active profile name and
   profile home under `HERMES_HOME` (or the host's equivalent discovery API),
   then construct `ApprovalStore(profile_home / "approvals.db",
   profile=active_profile)`. The adapter receives these resolved values; it
   does not guess or hard-code a machine path.
4. Build routes with `dashboard.plugin_api.build_router(store, dashboard_authorize)`.
5. Mount the returned router at `/api/plugins/approvals`.
6. Install `dashboard/` as the static dashboard payload and use the host SDK's
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
