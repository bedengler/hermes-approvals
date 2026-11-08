"""Exercise the supported Hermes dashboard's real plugin API mount path.

Run this test in the Hermes Agent checkout's uv environment with
HERMES_AGENT_REPO set. It deliberately isolates HERMES_HOME and the bundled
plugin root before importing the host, so no installed user data is touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _request(app, headers=()):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/plugins/approvals/pending",
            "raw_path": b"/api/plugins/approvals/pending",
            "query_string": b"",
            "headers": [
                (key.lower().encode(), value.encode()) for key, value in headers
            ],
            "server": ("testserver", 80),
            "client": ("testclient", 12345),
            "root_path": "",
            "app": app,
        }
    )


def test_supported_hermes_host_mounts_profile_scoped_approvals(
    tmp_path, monkeypatch, request
):
    host_repo_value = os.environ.get("HERMES_AGENT_REPO")
    if not host_repo_value:
        pytest.skip("set HERMES_AGENT_REPO and run in that Hermes Agent uv environment")
    host_repo = Path(host_repo_value).resolve()
    assert (host_repo / "hermes_cli" / "web_server_dashboard.py").is_file()
    assert (host_repo / "pyproject.toml").is_file()

    profile_name = "approval-host-test"
    hermes_root = tmp_path / "hermes-home"
    request.addfinalizer(lambda: shutil.rmtree(hermes_root, ignore_errors=True))
    profile_home = hermes_root / "profiles" / profile_name
    plugin_home = profile_home / "plugins" / "approvals"
    plugin_home.mkdir(parents=True)
    # Prevent host startup repair from copying a real default-profile .env
    # into this disposable test home.
    (profile_home / ".env").write_text("", encoding="utf-8")
    (profile_home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - approvals\n", encoding="utf-8"
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            (
                str(PROJECT_ROOT / "src"),
                str(PROJECT_ROOT),
                str(host_repo),
                os.environ.get("PYTHONPATH", ""),
            )
        ),
        "HERMES_DISABLE_LAZY_INSTALLS": "1",
    }
    install_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_approval.dashboard_installer",
            "install",
            "--profile",
            profile_name,
            "--hermes-home",
            str(hermes_root),
            "--source",
            str(PROJECT_ROOT / "dashboard"),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert install_result.returncode == 0, install_result.stderr
    assert "Installed" in install_result.stdout
    worker_home = hermes_root / "profiles" / "workerb"
    worker_home.mkdir(parents=True)
    (worker_home / ".env").write_text("", encoding="utf-8")
    (worker_home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - approvals\n", encoding="utf-8"
    )
    worker_install = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_approval.dashboard_installer",
            "install",
            "--profile",
            "workerb",
            "--hermes-home",
            str(hermes_root),
            "--source",
            str(PROJECT_ROOT / "dashboard"),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert worker_install.returncode == 0, worker_install.stderr
    empty_bundled_plugins = tmp_path / "empty-bundled-plugins"
    empty_bundled_plugins.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(empty_bundled_plugins))
    # This is source verification, not a request to reconcile a live Hermes
    # runtime or run its lazy install/update completion path.
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "1")
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "src"))
    monkeypatch.syspath_prepend(str(host_repo))
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join((str(PROJECT_ROOT / "src"), str(PROJECT_ROOT), str(host_repo))),
    )

    # Importing web_server invokes Hermes' own discovery/import/include_router
    # mechanism (_mount_plugin_api_routes), not a package-authored FastAPI app.
    from fastapi.testclient import TestClient
    from hermes_cli import web_server, web_server_dashboard

    plugins = web_server._get_dashboard_plugins(force_rescan=True)
    approval_plugin = next(
        plugin for plugin in plugins if plugin["name"] == "approvals"
    )
    assert approval_plugin["source"] == "user"
    assert approval_plugin["_api_file"] == "plugin_api.py"
    assert (
        json.loads((plugin_home / "dashboard" / "manifest.json").read_text())["version"]
        == "0.1.0"
    )
    assert (
        web_server_dashboard._plugin_api_mount_skip_reason(
            approval_plugin, {"approvals"}, set()
        )
        is None
    )

    adapter = sys.modules["hermes_dashboard_plugin_approvals"]
    expected_store = profile_home / "approvals.db"
    assert adapter._store.path == expected_store
    assert adapter._store.profile == profile_name
    assert expected_store.is_file()
    assert not (hermes_root / "approvals.db").exists()

    # The callback directly delegates to the host's canonical token/session
    # validators, while the mounted route also runs behind host middleware.
    web_server.app.state.auth_required = False
    assert adapter._authorize(
        _request(
            web_server.app,
            [(web_server._SESSION_HEADER_NAME, web_server._SESSION_TOKEN)],
        )
    )
    assert not adapter._authorize(_request(web_server.app))

    # The host auth middleware rejects the missing token with 401 before the
    # plugin guard runs (which independently denies unauthorized requests).
    with TestClient(web_server.app) as client:
        assert client.get("/api/plugins/approvals/pending").status_code == 401
        authorized = client.get(
            "/api/plugins/approvals/pending",
            headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
        )
        unauthorized_profile = client.get(
            "/api/plugins/approvals/pending",
            params={"profile": "workerb"},
            headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
        )
    assert authorized.status_code == 200
    assert authorized.json() == {"profile": profile_name, "items": []}
    assert unauthorized_profile.status_code == 403
    assert not (worker_home / "approvals.db").exists()
