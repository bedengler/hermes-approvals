# Hermes Approval API roadmap

## Basis and scope

No standalone evaluation report or scored results file is present in the current-main repository tree. This roadmap therefore uses the latest inspectable project evidence on current `main`: the package documentation, implementation, and checked-in tests. It records explicit integration and operations boundaries rather than assuming user priorities. The unchecked work is a set of candidate next steps, not a ranking.

## Verified on current main

- [x] Provide profile-scoped runtime approvals with version-checked decisions and atomic state/event updates. Evidence: `tests/test_store.py` covers profile isolation, stale/concurrent responses, transaction rollback, and persistence; `tests/test_api.py` exercises the HTTP DTO and response behavior.
- [x] Provide a host adapter seam and dashboard package payload, including governance approval views and confirmation flow. Evidence: `dashboard/plugin_api.py`, `dashboard/manifest.json`, `dashboard/dist/index.js`, `tests/test_api.py`, and `tests/test_packaging.py`.
- [x] Document a portable install, verification, and rollback path without claiming automatic live-host wiring. Evidence: `README.md` installation and Hermes host adapter sections; `MIGRATION.md` describes the manual host boundary.

## Candidate next steps

- [ ] Prove integration in a supported Hermes dashboard host. Done when an automated test or reproducible host verification exercises the actual host plugin-registration mechanism (not only a FastAPI fixture), confirms the active profile is bound to its own store, verifies the canonical authorization callback allows an authorized request and rejects an unauthorized request, and records the tested host/plugin version and exact verification command. `README.md` and `MIGRATION.md` currently identify live host wiring as manual and outside this package's tests.
- [ ] Connect retention cleanup to host maintenance. Done when the supported host's maintenance path invokes `ApprovalStore.cleanup()` for each active profile, an integration test proves cleanup leaves pending approvals intact while applying the configured resolved-record/event retention, and operator documentation names the maintenance entry point and its observable failure signal. `MIGRATION.md` currently asks hosts to schedule cleanup but does not provide or verify that host job.
