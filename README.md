# Hermes Approval API

Standalone, profile-scoped approval API foundation for a Hermes dashboard
integration. It is intentionally **not** a patch to Hermes core: this
repository does not publish, create remotes, execute approvals, restart
services, or modify the Hermes installation or any host-managed profile.

## Installation and activation

These instructions install the package and the dashboard payload; they do not
change Hermes core. The live host integration is intentionally a manual step.
Before starting, use a Hermes installation whose dashboard supports plugins
with a static `entry` payload (and optional `css`) plus an optional
module-level `api` adapter, and have Python 3.10+ plus
[uv](https://docs.astral.sh/uv/) available. The
dashboard/plugin host must also provide its normal plugin registry, profile
discovery, and authenticated request helpers.

### 1. Install the Python package

First identify the **exact Python executable running the dashboard**. Read the
dashboard service/launcher configuration or process supervisor to find the
executable and environment it starts; use that configured executable (an
absolute path), not the Python selected by your interactive shell, an unrelated
project/system Python, or a guessed virtual environment. Set it here:

```bash
DASHBOARD_PYTHON="/absolute/path/to/the/dashboard/python"
```

Explicitly target that interpreter when installing from the published
repository:

```bash
uv pip install --python "$DASHBOARD_PYTHON" "hermes-approval-api @ git+https://github.com/bedengler/hermes-approvals.git"
```

Or install a wheel (for example, one built with `uv build`) into that same
interpreter:

```bash
uv pip install --python "$DASHBOARD_PYTHON" ./dist/hermes_approval_api-*.whl
```

Verify imports and package version using the exact interpreter that runs the
dashboard:

```bash
"$DASHBOARD_PYTHON" -c 'import dashboard, hermes_approval, importlib.metadata; print("dashboard:", dashboard.__file__); print("hermes_approval:", hermes_approval.__file__); print("hermes-approval-api:", importlib.metadata.version("hermes-approval-api"))'
```

Both imports must succeed. Keep the reported paths and version for diagnostics.
If either import fails, reinstall into this interpreter and verify again before
continuing.

The wheel contains the importable `dashboard` adapter package, its
`manifest.json`, and the compiled `dist/index.js` bundle. A source checkout is
not required at runtime.

### 2. Install the dashboard plugin payload

Hermes discovers the plugin from the active Hermes home. Use the portable
default below, or set `HERMES_HOME` to the home used by the dashboard process
before copying the files:

```bash
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/approvals"
DASHBOARD_SRC="$("$DASHBOARD_PYTHON" -c 'import dashboard; print(dashboard.__path__[0])')"
mkdir -p "$PLUGIN_DIR"
cp -R "$DASHBOARD_SRC/." "$PLUGIN_DIR/"
```

This installs both the static dashboard plugin (`manifest.json` and
`dist/index.js`) and the host adapter (`plugin_api.py`) under
`${HERMES_HOME:-$HOME/.hermes}/plugins/approvals`. Do not copy an absolute
developer path into a manifest or host configuration. The copy step must use
the same `$DASHBOARD_PYTHON` as the package install and verification, so
`DASHBOARD_SRC` resolves to the payload bundled with the dashboard's package.

### 3. Bind the adapter to the active profile and canonical auth

The supported Hermes dashboard host loads the manifest's `api` module through
its dashboard plugin loader. `dashboard/plugin_api.py` binds the store to the
active Hermes profile home and delegates authorization to the host's canonical
session/query-token checks. The host-level registration, profile isolation,
and authorization behavior are exercised by
[`tests/test_supported_hermes_host.py`](tests/test_supported_hermes_host.py);
the exact tested host revision and command are recorded in
[`docs/host-integration.md`](docs/host-integration.md).

For another host that does not implement Hermes' manifest loader, use its
runtime profile API to resolve a profile home and inject its canonical
authorization callback into the reusable factory:

```python
from pathlib import Path

from hermes_approval.store import ApprovalStore
from dashboard.plugin_api import build_router

profile_name, profile_home = discover_active_profile()  # host API
store = ApprovalStore(
    Path(profile_home) / "approvals.db",
    profile=profile_name,
)
router = build_router(store, dashboard_authorize)
app.include_router(router, prefix="/api/plugins/approvals")
```

`discover_active_profile()` and `dashboard_authorize` above are placeholders
for non-Hermes hosts. The adapter does not read or mint credentials or bypass
middleware. The Hermes host supplies `/api/plugins/approvals` as the prefix;
do not duplicate it in the router. Other hosts must preserve their canonical
HTTP and WebSocket authorization paths rather than adding a second token
scheme.

### 4. Enable and reload the dashboard

Enable the `approvals` plugin through the dashboard's normal plugin settings or
registry, then reload/restart **only the Hermes dashboard process** so it
re-reads the manifest and adapter. Do not restart Hermes core or unrelated
services as part of this installation. If your host separates static plugin
discovery from backend registration, complete both documented host steps
before reloading the dashboard.

### 5. Verify the installation

Using the dashboard's canonical authenticated client (not an unauthenticated
browser or a copied token), request:

```text
GET /api/plugins/approvals/pending?limit=1
GET /api/plugins/approvals/history?limit=1
GET /api/plugins/approvals/governance/pending?limit=1
GET /api/plugins/approvals/governance/history?limit=1
```

Successful responses are JSON objects containing an `items` list (and the
runtime approval responses include the resolved profile). A `401`/`403` means
the host authorization callback correctly denied the request or the client is
not using the canonical dashboard session; fix that integration rather than
weakening authorization. A `404` usually means the plugin was not enabled or
the host mount is missing. `GET /events` and the event WebSocket are also
available as listed in the API contract below.

Runtime command approvals and governance approvals are different workflows.
Runtime approval responses resolve an approval request and this package never
executes a shell command. Governance actions delegate to the existing Hermes
governance decision function and require the dashboard authorization callback,
the exact approval ID, pending/TTL checks, and the UI's explicit second
confirmation. They remain subject to the host's existing policy and audit
controls; installing this plugin is not permission to approve governance
actions and does not replace those controls.

### Uninstall and rollback

To disable first, turn off the `approvals` plugin in the dashboard and reload
the dashboard. To remove the installed payload, verify the directory is the
intended plugin directory and then remove only that directory:

```bash
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/approvals"
test "${PLUGIN_DIR##*/}" = approvals && rm -rf "$PLUGIN_DIR"
```

Uninstall the Python package from the dashboard environment with
`uv pip uninstall hermes-approval-api`. Back up the profile database before
removing it; uninstalling the package or static payload does not delete
`approvals.db` or governance records. Roll back by reinstalling the previous
package version, restoring the previous plugin payload, and re-enabling only
after the dashboard has been reloaded.

### Troubleshooting package/runtime mismatches

- **The SPA loads, but its API is missing or dashboard startup logs show
  `ModuleNotFoundError` (for example, `No module named 'hermes_approval'`) or
  adapter/mount errors:** the static page can load even when its Python adapter
  cannot. Re-identify the exact dashboard executable from its service/launcher
  configuration, set `$DASHBOARD_PYTHON` to that path, and run the import and
  version check above. If it fails, install with
  `uv pip install --python "$DASHBOARD_PYTHON" ...`, repeat the check, and copy
  the plugin payload using that same interpreter. Then reload/restart **only
  the dashboard** and check its startup logs for import and route-mount errors.
  A Hermes update could cause this only if it replaced or switched the
  dashboard environment; an incident where the active environment lacked the
  dependency does not by itself establish that an update caused it.
- **API returns `404`:** check plugin enablement and router mounting/path
  configuration. **API returns `401` or `403`:** the canonical dashboard
  authorization rejected the request; check the authenticated session and
  host callback. Do not weaken or bypass authentication to address either
  result.
- **Manifest or bundle not found:** repeat the copy step from that same
  environment and verify `${PLUGIN_DIR}/manifest.json` and
  `${PLUGIN_DIR}/dist/index.js` exist. Do not point the host at a checkout-only
  `dashboard/` path.
- **Routes missing or duplicated:** verify the host loaded `plugin_api.py`,
  mounted the returned router exactly at `/api/plugins/approvals`, and did not
  add that prefix inside the router.
- **Authorization failures:** keep the dashboard's canonical callback and
  session middleware; do not add ad-hoc credentials or disable the callback.
- **Stale UI after an upgrade:** disable/re-enable the plugin and reload only
  the dashboard, then check that the manifest version and bundle came from the
  same package installation.

### Handling approvals during repair

Hermes Agent's built-in dangerous-command approval is separate from the
optional Hermes Company governance workflow. For an upstream Hermes CLI
command prompt, review the exact command and choose `once` (the `o` option) for
a one-off repair; the prompt also offers session, always, and deny. `always`
persists an allowlist rule, so do not select it just to complete a repair. In
Gateway or messaging, reply `yes`, `y`, `approve`, `ok`, or `go` to approve the
pending command;
unapproved commands are denied by default. See the [Hermes Agent security
documentation](https://hermes-agent.nousresearch.com/docs/user-guide/security).
Do not use `--yolo`, turn approvals off, or grant broad/permanent approval as a
repair workaround.

Only users with the optional Hermes Company `~/.hermes/company/` customization
have the separate governance approval flow and `apr_...` IDs. Those approvals
are not native Hermes shell prompts, and a dashboard-plugin failure does not
remove the Company governance CLI. Review the exact pending ID, action, and
scope first by listing pending requests:

```bash
python3 ~/.hermes/company/integration/governance_mcp/approvals.py list
```

Approve only the intended request; deny it and stop if it is unexpected. To
approve a reviewed request manually, replace `approval_ID` with its actual
pending ID and omit the square brackets:

```bash
python3 ~/.hermes/company/integration/governance_mcp/approvals.py approve [approval_ID]
```

Users without this optional customization do not have or need that script or
the `apr_...` flow; approve or deny Hermes' native command prompt in the
session UI instead. The Company CLI is not part of upstream Hermes and is not
required to use this plugin.

## API contract

The host mounts the router at `/api/plugins/approvals`. These are router-relative
paths; do not add the prefix twice:

- `GET /api/plugins/approvals/pending?limit=1..500`
- `GET /api/plugins/approvals/history?limit=1..500`
- `POST /api/plugins/approvals/{request_id}/respond`
- `GET /api/plugins/approvals/governance/pending?limit=1..500`
- `GET /api/plugins/approvals/governance/history?limit=1..500`
- `POST /api/plugins/approvals/governance/{approval_id}/respond` with
  `{"decision":"approve"|"deny", "note":"optional"}`
- `GET /api/plugins/approvals/events?after_id=N&limit=1..500`
- `WS /api/plugins/approvals/events/stream`

Responses use explicit DTOs. Internal `session_id` is never returned in items,
responses, or event payloads. Limits are positive and bounded; no unbounded or
`LIMIT -1` query is accepted.

## Security and state

- Explanations and source labels are redacted before persistence, including
  shell and key/value forms of token/password/API-key credentials,
  `client_id`/`client-id`, `client_secret`/`client-secret`, `private_key`/
  `private-key`, `--client-secret value`, `--private-key=value`, Bearer, and
  Basic. Quoted JSON-style credential keys are also recognized with either
  quoted or unquoted textual values (for example, `"token":"value"` or
  `"token":value`). Quoted values with spaces are accepted, and PEM
  private-key blocks are consumed through their matching END marker across
  newlines, while malformed or unterminated blocks consume the remainder of
  the input fail-closed. This is an accepted textual policy, not an arbitrary structured
  JSON/YAML sanitizer.
  Text is then limited to 2,000 characters. Ordinary prose is preserved unless
  it uses a credential-looking option or key/value/header form.
- Hosts inject the canonical dashboard authorization callback; this package
  does not read or mint credentials. HTTP and WebSocket access use that seam.
  The WebSocket checks the callback before each polling pass, but revocation
  behavior still depends on the host callback reflecting the live session
  state; live host middleware integration is outside this package's tests.
 - Governance decisions are separately scoped from runtime command approvals.
 The host adapter injects the existing governance module's `decide` function;
 the HTTP handler never invokes a shell command. Decisions require the
 dashboard authorization callback, exact `approval_id`, pending/TTL checks,
 and return `404` for unknown IDs or `409` for expired/already-decided IDs.
 The UI displays an explicit second confirmation containing the exact ID, gate,
 target, expiry, selected decision, and server-redacted rationale. Approve/Deny
 controls and Refresh expose pointer and keyboard-focus affordances. Refresh is
 disabled while loading and reports loading, last-refresh/no-change, empty, and
 safe error states. Governance actions show success, stale/expired, not-found,
 and generic failure outcomes, then refresh pending/history without claiming a
 change when none occurred.
- Responses use opaque request IDs and compare-and-swap version checks. The
  approval mutation and `approval.resolved` event insert commit in one SQLite
  transaction, with database busy handling and profile scoping.
- `store.cleanup()` is the retention mechanism: by default it deletes resolved
  approvals after 90 days and keeps only the newest 10,000 events per profile.
  `retention_days` must be an integer from 1 through 3,650, and `max_events`
  must be an integer from 1 through 100,000; booleans, floats, strings, zero,
  negatives, and values above those limits are rejected. Events are not
  age-pruned; their retention is controlled only by `max_events`. Schedule
  cleanup from the host maintenance job; see `MIGRATION.md`.

## Hermes host adapter boundary

The supported Hermes host's manifest loader imports `dashboard/plugin_api.py`
and mounts its module-level `router`. That adapter resolves the active
profile home and uses Hermes' canonical dashboard authentication checks. It
does not embed a username or absolute machine path. This integration is
verified against the exact checkout in `docs/host-integration.md`; it is not a
live-host rollout or a claim that the plugin is deployed. Other host versions
that do not implement this manifest contract can use the factory below:

```python
from dashboard.plugin_api import build_router

router = build_router(profile_store, dashboard_authorize)
app.include_router(router, prefix="/api/plugins/approvals")
```

The reusable router behavior is covered by `tests/test_api.py`; the actual
Hermes host registration path is covered by `tests/test_supported_hermes_host.py`.

## Verification

```bash
uv run --extra test pytest -q
node --check dashboard/dist/index.js
git diff --check
uv build
```

The wheel includes the importable `dashboard` adapter package plus
`manifest.json` and the compiled dashboard bundle; no checkout-relative
`dashboard/` directory is needed after installation. `tests/test_packaging.py`
builds a wheel in a temporary directory and inspects those exact entries.

`uv.lock` is retained for reproducibility.

## License

This project is licensed under the Apache License, Version 2.0. See
[`LICENSE`](LICENSE) for the complete text. Redistribution must include a copy
of that license and retain the repository's copyright and attribution notices;
modified files should carry prominent notices describing the changes. The
repository does not include a `NOTICE` file because its current project
content has no additional attribution notices requiring one. Any third-party
notices introduced by future dependencies or contributions remain subject to
their applicable license terms.
