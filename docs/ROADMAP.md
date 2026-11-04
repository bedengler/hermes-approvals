# Hermes Approval API roadmap

## Basis and scope

No standalone evaluation report or scored results file is present in the current-main repository tree. This roadmap therefore uses the latest inspectable project evidence on current `main`: the package documentation, implementation, and checked-in tests. It records explicit integration and operations boundaries rather than assuming user priorities. The unchecked work is a set of candidate next steps, not a ranking.

## Verified on current main

- [x] Provide profile-scoped runtime approvals with version-checked decisions and atomic state/event updates. Evidence: `tests/test_store.py` covers profile isolation, stale/concurrent responses, transaction rollback, and persistence; `tests/test_api.py` exercises the HTTP DTO and response behavior.
- [x] Provide a host adapter seam and dashboard package payload, including governance approval views and confirmation flow. Evidence: `dashboard/plugin_api.py`, `dashboard/manifest.json`, `dashboard/dist/index.js`, `tests/test_api.py`, and `tests/test_packaging.py`.
- [x] Document a portable install, verification, and rollback path without claiming automatic live-host wiring. Evidence: `README.md` installation and Hermes host adapter sections; `MIGRATION.md` describes the manual host boundary.

## Candidate next steps

- [x] Prove integration in a supported Hermes dashboard host. Done: `tests/test_supported_hermes_host.py` exercises the actual Hermes plugin loader/mount path, verifies active-profile store binding and canonical auth allow/deny behavior, and records exact tested host/plugin versions plus the reproduction command in `docs/host-integration.md`. This is source-level proof only, not a live-host rollout.
- [x] Connect retention cleanup to host maintenance. `dashboard/approval_cleanup.py` is a supported Hermes no-agent cron script entry point; the host's multiplexed cron runner executes the job in each profile's own scope. `tests/test_supported_hermes_maintenance.py` exercises Hermes' real cron `run_job()` path for multiple profile homes, verifies each profile's configured resolved-record/event retention and pending-record preservation, and proves invalid config surfaces as a failed cron run. `src/hermes_approval/cleanup_installer.py` provides a dry-run, idempotent, profile-explicit installer and rollback through Hermes' local cron CLI; `tests/test_cleanup_installer.py` covers repeat install, isolation, dry-run, and own-entry removal using temporary homes. `MIGRATION.md` documents installation and failure inspection. This remains operator-triggered and is not an automatic live-host rollout.
