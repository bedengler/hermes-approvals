# Hermes Approval API roadmap

## Basis and scope

No standalone evaluation report or scored results file is present in the current-main repository tree. This roadmap therefore uses the latest inspectable project evidence on current `main`: the package documentation, implementation, and checked-in tests. It records explicit integration and operations boundaries rather than assuming user priorities. The unchecked work is a set of candidate next steps, not a ranking.

## Verified on current main

- [x] Provide profile-scoped runtime approvals with version-checked decisions and atomic state/event updates. Evidence: `tests/test_store.py` covers profile isolation, stale/concurrent responses, transaction rollback, and persistence; `tests/test_api.py` exercises the HTTP DTO and response behavior.
- [x] Provide a host adapter seam and dashboard package payload, including governance approval views and confirmation flow. Evidence: `dashboard/plugin_api.py`, `dashboard/manifest.json`, `dashboard/dist/index.js`, `tests/test_api.py`, and `tests/test_packaging.py`.
- [x] Document a portable install, verification, and rollback path without claiming automatic live-host wiring. Evidence: `README.md` installation and Hermes host adapter sections; `MIGRATION.md` describes the manual host boundary.

## Candidate next steps

- [x] Prove integration in a supported Hermes dashboard host. Done: `tests/test_supported_hermes_host.py` exercises the actual Hermes plugin loader/mount path, verifies active-profile store binding and canonical auth allow/deny behavior, and records exact tested host/plugin versions plus the reproduction command in `docs/host-integration.md`. This is source-level proof only, not a live-host rollout.
- [x] Add an operator-invoked, reversible dashboard payload installer for one explicitly selected existing Hermes profile. Done criterion: CLI `install --dry-run` changes no files, deliberate `install` is idempotent, and `uninstall` removes only unchanged installer-owned payload files while preserving sibling plugin/job data and neighboring profiles. Evidence: `src/hermes_approval/dashboard_installer.py` and `tests/test_dashboard_installer.py` (temporary Hermes-home fixtures); packaging includes the command module (`tests/test_packaging.py`). README and MIGRATION document dry-run/install/uninstall. The operator still enables/reloads manually; this is not an automatic live-host rollout.
  Verification: `uv run --extra test pytest -q tests/test_packaging.py tests/test_supported_hermes_host.py tests/test_cleanup_installer.py` -> `7 passed, 1 skipped`; `uv run --extra test pytest -q tests/test_dashboard_installer.py` -> `5 passed`. The host-source test separately passed at the recorded host commit; `MIGRATION.md` records the warning and environment limitation. `project_health.py check` reports the repository's existing `WEB_NO_SECURITY_HEADERS` high finding; no web/security code changed in this installer step.
