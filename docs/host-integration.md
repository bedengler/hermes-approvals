# Supported Hermes dashboard host integration proof

This records a source-level integration check only. It does not install the
plugin into the user's live Hermes home, enable it there, or claim deployment.

## Tested revisions

- Hermes Agent host checkout: `/Users/bedenglerai/.hermes/hermes-agent`
- Host source version: output of `git describe --tags --always --dirty` was
  `v0.21.4+canary.20261001T070239Z-58-gf2ae4a83a3`
- Exact host commit: `f2ae4a83a3051f38f6d33f8c71c3c54cb3418ee5`
- Approval dashboard plugin manifest version: `0.1.0`

The host checkout was clean before and after verification. The verifier copies
the package dashboard payload into a disposable temporary `HERMES_HOME`,
enables only that test plugin, and sets an empty bundled-plugin directory.
Hermes' actual `_mount_plugin_api_routes()` imports the manifest-declared module
and mounts its router in the host FastAPI app. The test checks the active
profile's database path and identity (including a second `?profile=`-scoped
profile), exercises the adapter's canonical session-token authorization callback
for both allow and deny, then makes unauthorized and authorized requests through
the mounted host app.

## Reproduction command

Run from the approval API checkout, with the host checkout above available:

```sh
HERMES_AGENT_REPO=/Users/bedenglerai/.hermes/hermes-agent \
PYTHONPATH=/Users/bedenglerai/hermes-approval-api/.worktrees/t_081be571/src \
HERMES_DISABLE_LAZY_INSTALLS=1 \
uv run --locked --project /Users/bedenglerai/.hermes/hermes-agent --with pytest \
python -m pytest -vv -s \
/Users/bedenglerai/hermes-approval-api/.worktrees/t_081be571/tests/test_supported_hermes_host.py
```

Actual result: `1 passed, 1 warning in 1.68s`. The warning is Starlette's
`TestClient` deprecation notice for the host environment's `httpx`; it does not
fail the integration check. `HERMES_DISABLE_LAZY_INSTALLS=1` prevents this
source-verification command from starting Hermes' lazy runtime update path.
