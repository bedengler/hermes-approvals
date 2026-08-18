# Hermes Approval API

Standalone, local GitHub-ready foundation for a profile-scoped approvals surface. It
is intentionally an integration package, not a patch to Hermes core and it does
not publish, create remotes, or restart services.

## Design

- **Opaque IDs:** `secrets.token_urlsafe` request IDs and nonces; session IDs are
  never exposed as identifiers in the dashboard contract.
- **Optimistic concurrency:** every pending item has `version` + `nonce`; POST
  requires `expected_version` (and may require `nonce`). A stale or second action
  returns HTTP 409 and cannot execute twice.
- **Durable history:** SQLite stores pending and resolved rows plus append-only
  created/resolved events. The database path is supplied by the profile host.
- **Safe explanations:** token/password/API-key/Bearer values are redacted and
  explanation text is length bounded before persistence and display.
- **Authorization boundary:** `create_router(..., authorize=...)` requires the
  host's canonical dashboard authorization callback. No credentials are read or
  minted here. The same store's `resolve_legacy_session` preserves the existing
  Discord/TUI oldest-pending-by-session behavior during migration.
- **Live events:** polling endpoint and WebSocket stream are provided. The host
  should put its existing auth middleware in front of the mounted router.

## API

`GET /approvals/pending` → `{profile, items[]}`

`GET /approvals/history?limit=100` → durable resolved items

`POST /approvals/{request_id}/respond`

```json
{"decision":"approve", "expected_version":1, "nonce":"optional-nonce"}
```

Returns the resolved item; `403` authorization failure, `404` unknown ID, and
`409` stale/double action. `GET /approvals/events?after_id=N` and
`GET /approvals/events/stream` expose created/resolved events.

## Hermes integration boundary

1. Add this directory (or its package) under a user plugin checkout.
2. Construct `ApprovalStore(profile_home / "approvals.db", profile=active_profile)`
   from the active Hermes profile, not a global hardcoded path.
3. Feed the existing dashboard session authorization dependency to
   `dashboard.plugin_api.build_router(store, dashboard_authorize)`.
4. Make the approval manager write through `store.create`, resolve through
   `store.respond`, and retain `resolve_legacy_session` for existing Discord and
   `approval.respond` callers.
5. Mount the router using the host's normal `/api/plugins/approvals` mechanism and
   install `dashboard/` as the plugin payload. Do not bypass dashboard auth.

This repository deliberately does **not** modify `/Users/bedenglerai/.hermes/hermes-agent`:
upstream wiring needs a separate reviewed change because the current approval
queue is private/in-memory and its resolver has no stable request ID.

## Local verification

From this directory (with a Python environment containing `pytest`, `fastapi`,
and `httpx`):

```bash
python3 -m pytest -q
node --check dashboard/dist/index.js
git diff --check
```

No production service is started by this repository.

## Release checklist

- independent security/spec review
- integrate store with Hermes approval manager and shared auth gate
- add real gateway/Discord adapter integration tests against a temp profile
- verify migration and concurrent responders under SQLite load
- package metadata/CI and choose repository identity/visibility
- only then obtain separate publication approval
