# Supported Hermes dashboard host integration proof

This records a source-level integration check only. It does not install the
plugin into the user's live Hermes home, enable it there, or claim deployment.

## Tested revisions

- Hermes Agent host checkout: `${HERMES_AGENT_REPO:-$HOME/.hermes/hermes-agent}`
- Host source version: output of `git describe --tags --always --dirty` was
  `v0.23.0+canary.20261108T064411Z-157-g2a9bb2fbaf`
- Exact host commit: `2a9bb2fbaf47845f937465c94c5b9b502b81c0f0`
- Approval dashboard plugin manifest version: `0.1.0`

The host checkout was clean before and after verification. The verifier invokes
the package's actual installer CLI against a disposable temporary Hermes home,
enables only that test plugin, and sets an empty bundled-plugin directory.
Hermes' actual `_mount_plugin_api_routes()` imports the manifest-declared module
and mounts its router in the host FastAPI app. The test checks the selected
profile's database path and identity, exercises the adapter's canonical
session-token authorization callback for both allow and deny, and checks that a
request selecting another profile without that profile's grant is refused. It
does not run the installer against or modify a live profile.

## Reproduction command

Run from the approval API checkout, with the host checkout above available:

```sh
export HERMES_AGENT_REPO="${HERMES_AGENT_REPO:-$HOME/.hermes/hermes-agent}"
export HERMES_DISABLE_LAZY_INSTALLS=1
uv run --locked --project "$HERMES_AGENT_REPO" --with pytest \
python -m pytest -vv -s "$PWD/tests/test_supported_hermes_host.py"
```

Actual result on Hermes Agent commit
`2a9bb2fbaf47845f937465c94c5b9b502b81c0f0`: `1 passed, 1 warning in 3.15s`.
The warning is Starlette's `TestClient` deprecation notice for the host
environment's `httpx`; it does not fail the integration check.
`HERMES_DISABLE_LAZY_INSTALLS=1` prevents this source-verification command from
starting Hermes' lazy runtime update path.

## Retention maintenance integration proof

The same supported host revision also provides the no-agent cron script
maintenance path. `tests/test_supported_hermes_maintenance.py` executes the
copied package entry point through Hermes' actual `cron.scheduler.run_job()`
under `_profile_cron_scope()` for two profile homes with distinct retention
configuration, then exercises a failing configuration and checks the cron
failure result. The test uses disposable homes; it does not alter the host
checkout or any user profile.

Run both supported-host checks from the approval API checkout:

```sh
export HERMES_AGENT_REPO="${HERMES_AGENT_REPO:-$HOME/.hermes/hermes-agent}"
export HERMES_DISABLE_LAZY_INSTALLS=1
uv run --locked --project "$HERMES_AGENT_REPO" --with pytest \
python -m pytest -vv -s \
  "$PWD/tests/test_supported_hermes_host.py" \
  "$PWD/tests/test_supported_hermes_maintenance.py"
```

Verified against Hermes Agent commit
`2a9bb2fbaf47845f937465c94c5b9b502b81c0f0`
(`v0.23.0+canary.20261108T064411Z-157-g2a9bb2fbaf`). Actual result:
`2 passed, 1 warning in 4.17s`; the warning is the existing Starlette
`TestClient` deprecation notice for `httpx`. Hermes cron reports a non-zero
script exit as a failed run and sends the configured failure alert; operator
inspection commands are documented in `MIGRATION.md`.
