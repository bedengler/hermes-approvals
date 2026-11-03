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
3. On the supported Hermes dashboard host, install the package's dashboard
   payload under the plugin directory and enable `approvals` through the host's
   normal plugin settings. The host discovers `dashboard/manifest.json`,
   imports its declared `api` module, and mounts its module-level `router` at
   `/api/plugins/approvals`. The adapter obtains the active Hermes profile
   home and name in the request context so the host's `?profile=` scope selects
   the matching store, and uses the canonical dashboard authentication checks.
4. For a different host without Hermes' manifest loader, resolve the active
   profile and canonical auth callback using that host's runtime APIs, then use
   `dashboard.plugin_api.build_router(store, dashboard_authorize)` and mount it
   at `/api/plugins/approvals`.
5. Install `dashboard/` as the static payload and use the host SDK's
   authenticated `fetchJSON`/plugin registry APIs.

`tests/test_supported_hermes_host.py` drives the actual Hermes dashboard plugin
discovery/import/`include_router` path in an isolated test home and verifies
profile binding plus authorization. `docs/host-integration.md` records the
host revision and exact command. This is source-level integration evidence,
not a live-host rollout.

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
