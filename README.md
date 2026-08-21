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

Install into the same Python environment used by the Hermes dashboard host.
Either install directly from the published repository:

```bash
uv pip install "hermes-approval-api @ git+https://github.com/bedengler/hermes-approvals.git"
```

or install a wheel (for example, one built with `uv build`):

```bash
uv pip install ./dist/hermes_approval_api-*.whl
```

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
DASHBOARD_SRC="$(python3 -c 'import dashboard; print(dashboard.__path__[0])')"
mkdir -p "$PLUGIN_DIR"
cp -R "$DASHBOARD_SRC/." "$PLUGIN_DIR/"
```

This installs both the static dashboard plugin (`manifest.json` and
`dist/index.js`) and the host adapter (`plugin_api.py`) under
`${HERMES_HOME:-$HOME/.hermes}/plugins/approvals`. Do not copy an absolute
developer path into a manifest or host configuration. If the dashboard runs
under a different environment, perform the package install and copy step from
that environment so `DASHBOARD_SRC` resolves to the matching installation.

### 3. Bind the adapter to the active profile and canonical auth

At dashboard startup, the host must discover the active profile and profile
home using its normal runtime profile API (with `HERMES_HOME` as the optional
home override). Construct a profile-scoped store, then inject the dashboard's
existing authorization callback into the adapter:

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

`discover_active_profile()` and `dashboard_authorize` above are host-provided
placeholders: use the actual APIs supplied by the Hermes dashboard. The
adapter does not discover profiles by guessing, read credentials, mint
credentials, or bypass middleware. The host must mount the returned router at
`/api/plugins/approvals`; the routes in this README are relative to that
prefix. Preserve the canonical dashboard authorization path for HTTP and
WebSocket requests rather than adding a second token scheme.

The manifest's `api: "plugin_api.py"` identifies the module-level adapter to
hosts that support that manifest contract. Installing the files does **not** by
itself prove that a particular Hermes build will register or bind the adapter:
if the host requires an explicit registry/adapter hook, configure that hook
manually using its documented plugin API. Do not duplicate the
`/api/plugins/approvals` prefix in the router.

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

- **Import or `ModuleNotFoundError` errors:** confirm the dashboard and
  `uv pip install` use the same Python environment; run
  `python3 -c 'import dashboard, hermes_approval; print(dashboard.__file__); print(hermes_approval.__file__)'`.
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

The actual Hermes host supports dashboard manifests with a static `entry`
payload (and optional `css`) and an optional module-level `api` router. This
plugin ships a small host adapter at `dashboard/plugin_api.py`; the host
integration must resolve the active profile and its home at runtime, then bind
that store and the canonical dashboard authorization callback. It does not
embed a username or absolute machine path. The manifest declares
`"api": "plugin_api.py"`, but installing it does not automatically wire live
host integration on every Hermes build. Hosts that support this manifest
contract may load the adapter; otherwise register it manually with the host
plugin API:

```python
from dashboard.plugin_api import build_router

router = build_router(profile_store, dashboard_authorize)
app.include_router(router, prefix="/api/plugins/approvals")
```

The route-mounting/auth integration test is in `tests/test_api.py`. This is a
host-adapter installation path, not live Hermes wiring.

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
