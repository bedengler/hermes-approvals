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
3. On the supported Hermes dashboard host, use the bundled operator-invoked
   installer to copy the payload into one explicitly selected existing profile.
   It writes only `plugins/approvals/dashboard/`; it does not enable the plugin,
   modify configuration, restart the host, or touch another profile. The host
   discovers `dashboard/manifest.json`, imports its declared `api` module, and
   mounts its module-level `router` at `/api/plugins/approvals`. The adapter
   obtains the active Hermes profile home and name in the request context so the
   host's `?profile=` scope selects the matching store, and uses canonical
   dashboard authentication checks.
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

## Operator-invoked dashboard payload installer

The installer is a deliberate local package operation, not a deployment. The
`hermes-approval-api` Python package must already be installed in the same
interpreter that runs the dashboard. Set that interpreter and the Hermes root,
then choose exactly one existing profile. For example, with `PROFILE=alpha`:

```bash
DASHBOARD_PYTHON="/absolute/path/to/the/dashboard/python"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PROFILE="alpha"                  # or default
DASHBOARD_SRC="$("$DASHBOARD_PYTHON" -c 'import dashboard; print(dashboard.__path__[0])')"
"$DASHBOARD_PYTHON" -m hermes_approval.dashboard_installer install \
  --profile "$PROFILE" --hermes-home "$HERMES_HOME" \
  --dashboard-source "$DASHBOARD_SRC" --dry-run
# Review target and planned counts, then deliberately install:
"$DASHBOARD_PYTHON" -m hermes_approval.dashboard_installer install \
  --profile "$PROFILE" --hermes-home "$HERMES_HOME" \
  --dashboard-source "$DASHBOARD_SRC"
```

The installer requires the selected profile to exist and writes payload files
only to `plugins/approvals/dashboard/` in that profile. It refuses existing
unowned payload files, records hashes for files it owns, and rejects later
replacement of locally edited owned files. Repeat installation is idempotent.
It does not enable the plugin, alter host configuration, install Python
packages, reload/restart Hermes, touch another profile, or delete approval
data. Enable/reload through the host's normal operator workflow only when
ready; this remains a manual host boundary with no automatic live rollout.

To preview and then roll back this install, use `uninstall` for the same
profile; the dry run is again read-only:

```bash
"$DASHBOARD_PYTHON" -m hermes_approval.dashboard_installer uninstall \
  --profile "$PROFILE" --hermes-home "$HERMES_HOME" --dry-run
"$DASHBOARD_PYTHON" -m hermes_approval.dashboard_installer uninstall \
  --profile "$PROFILE" --hermes-home "$HERMES_HOME"
```

Uninstall removes only unchanged files listed by its ownership receipt,
preserves locally changed and unowned files, and prunes only empty directories
that this installer created. To roll back to an earlier package version, run the install action
with that earlier payload. `approvals.db`, configuration, and the Python
package are outside this installer's ownership scope. Automated installer
coverage uses temporary `HERMES_HOME` fixtures only. Verification from the package
checkout:

```bash
uv run --extra test pytest -q tests/test_packaging.py tests/test_supported_hermes_host.py tests/test_cleanup_installer.py
uv run --extra test pytest -q tests/test_dashboard_installer.py
```

Actual results: the requested focused command reports `7 passed, 1 skipped in
19.67s`; the skip is the supported host-source check when `HERMES_AGENT_REPO` is
unset. The new installer tests report `5 passed in 0.91s`. The host-source check
separately passed (`1 passed, 1 warning in 2.50s`) against the recorded host
commit `f2ae4a83a3051f38f6d33f8c71c3c54cb3418ee5` using an isolated source archive
and temporary `HERMES_HOME`; the warning is the host's existing Starlette/httpx
`TestClient` deprecation notice. No host profile was modified.

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

Use the bundled offline installer to copy the script and register the job in
one explicitly selected profile. The profile must already exist in Hermes.
`--hermes-home` is the Hermes root (not the selected profile's home); the
installer resolves `default` to that root and named profiles to
`profiles/<name>`. It invokes only Hermes' local `cron create`/`cron remove`
commands: installation never runs the cleanup script, contacts a model, or
changes another profile. `--dry-run` reads the selected profile's job store
and prints the planned operation without creating directories, copying files,
or invoking Hermes.

```bash
PROFILE=default                    # or an existing named profile, e.g. alpha
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_CLI="$(command -v hermes)"
"$DASHBOARD_PYTHON" -m hermes_approval.cleanup_installer install \
  --profile "$PROFILE" \
  --hermes-home "$HERMES_HOME" \
  --hermes "$HERMES_CLI" \
  --script-source "$DASHBOARD_SRC/approval_cleanup.py" \
  --interpreter "$DASHBOARD_PYTHON" \
  --dry-run
# After inspecting the preview, repeat the command without --dry-run to install.
```

The default schedule is `0 3 * * *` (Hermes' configured profile timezone).
Pass `--schedule '5 3 * * *'` to choose a different schedule and pass the same
value for removal. Re-run the install command to verify an existing identical
job or complete a prior install; a same-name mismatch is refused. To roll back,
repeat the exact paths, interpreter, and schedule with `uninstall` instead of
`install`; for example:

```bash
"$DASHBOARD_PYTHON" -m hermes_approval.cleanup_installer uninstall \
  --profile "$PROFILE" \
  --hermes-home "$HERMES_HOME" \
  --hermes "$HERMES_CLI" \
  --script-source "$DASHBOARD_SRC/approval_cleanup.py" \
  --interpreter "$DASHBOARD_PYTHON"
```

It removes only the matching installer job and removes the copied script only
when its bytes still match the selected source and no remaining job references
it. Conflicting or edited entries are left untouched.

Repeat for each active profile, changing `PROFILE` for each invocation. The
script reads optional per-profile `approvals.retention_days` and
`approvals.max_events` settings from that profile's `config.yaml`; omitted
settings use 90 days and 10,000 events. For example:

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

## Isolated real Hermes CLI test

`tests/test_cleanup_installer.py` runs the installer end to end against Hermes'
real CLI entry point (not a stub) in a disposable environment: `HOME`,
`HERMES_HOME`, both profiles, their cron stores, the CLI virtual environment,
and the uv cache all live under pytest's temporary directory. It creates a
`sibling-maintenance` job inside `alpha` and a second profile `beta` with its
own job and script, then checks install, repeat-install idempotence (store
document and script bytes unchanged), uninstall, that only
`approvals-retention-cleanup` disappears while the sibling job stays, and that
`beta` and its script remain unchanged. Before installation it runs the
installer's dry-run entry point and snapshots the temporary Hermes home to prove
that no files or directories changed. It never invokes the host-installed CLI
and never touches a live profile or cron store.

The test needs a Hermes Agent source checkout and `uv`; without either it
skips with an explicit reason, which is what CI does. Run it locally with:

```bash
export HERMES_AGENT_REPO="${HERMES_AGENT_REPO:-$HOME/.hermes/hermes-agent}"
uv run --group test pytest -q tests/test_cleanup_installer.py -k real_cli
```

Its temporary launcher runs `hermes_cli.main` from that checkout with
`uv run --locked --project "$HERMES_AGENT_REPO" --with ruamel.yaml==0.18.16
python -m hermes_cli.main "$@"`, which keeps the host CLI's YAML dependency
inside the disposable test runtime instead of the host environment. Actual
result of the command above on a machine with the checkout present:
`1 passed, 5 deselected in 13.75s`; the full suite with the checkout present is
`317 passed, 2 skipped`, and without it (CI) `316 passed, 3 skipped`. This is
isolated CLI verification only, not installation into or rollout on a live
Hermes host.
