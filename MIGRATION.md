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
profile database preserves pending/history/events. The supported Hermes
maintenance entry point is a per-profile no-agent cron job. Hermes' gateway
cron multiplexer ticks each served profile's own job store under that profile's
home; create the job in every active profile. The job executes the package's
`dashboard/approval_cleanup.py` script, which delegates to
`hermes_approval.maintenance.cleanup_active_profile()` and calls
`ApprovalStore.cleanup()` for the profile scoped by Hermes.

Install the script into each profile's scripts directory using the same
`DASHBOARD_PYTHON` and `DASHBOARD_SRC` from the README installation steps:

```bash
PROFILE=default
# Set PROFILE_HOME to this profile's actual Hermes home. For the default
# profile this is usually "$HOME/.hermes"; a named profile is usually under
# "$HOME/.hermes/profiles/$PROFILE".
PROFILE_HOME="$HOME/.hermes"
export HERMES_HOME="$PROFILE_HOME"
mkdir -p "$HERMES_HOME/scripts"
cp "$DASHBOARD_SRC/approval_cleanup.py" "$HERMES_HOME/scripts/approval-cleanup.py"
hermes -p "$PROFILE" cron create "0 3 * * *" \
  --no-agent --script approval-cleanup.py \
  --interpreter "$DASHBOARD_PYTHON" \
  --name approvals-retention-cleanup
```

Repeat for every active profile, setting `PROFILE` and `PROFILE_HOME` to that
profile before copying the script and creating its job. The script reads
optional per-profile `approvals.retention_days` and `approvals.max_events`
settings from that profile's `config.yaml`; omitted settings use 90 days and
10,000 events. For example:

```yaml
approvals:
  retention_days: 90
  max_events: 10000
```

The resolved-record retention must be an integer from 1 through 3,650, and the
event limit an integer from 1 through 100,000. Invalid values or database
errors make the script exit non-zero. Hermes cron records the failed run and
emits its configured failure alert; inspect the profile's `hermes cron runs
<job-id>` output and `hermes cron status` for the failure details.

Resolved approvals older than the configured retention are deleted; pending
approvals are never deleted. Events have no age-based policy: `max_events` is
their sole retention limit, so an event may outlive its resolved approval row.
Cleanup success prints the profile, configured limits, and deleted event count.
