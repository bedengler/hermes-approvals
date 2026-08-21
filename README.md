# Hermes Approval API

Standalone, profile-scoped approval API foundation for a Hermes dashboard
integration. It is intentionally **not** a patch to Hermes core: this
repository does not publish, create remotes, execute approvals, restart
services, or modify the Hermes installation or any host-managed profile.

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

The actual Hermes host supports dashboard manifests with static `entry`/`css`
assets and an optional module-level `api` router. This plugin ships a small
host adapter at `dashboard/plugin_api.py`; the host integration resolves the
active profile and its home at runtime, then binds that store and the canonical
dashboard authorization callback. It does not embed a username or absolute
machine path. The manifest declares `"api": "plugin_api.py"` and Hermes mounts
it automatically:

```python
from dashboard.plugin_api import router

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
