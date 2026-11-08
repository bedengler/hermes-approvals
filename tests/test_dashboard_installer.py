from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hermes_approval import dashboard_installer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_SOURCE = PROJECT_ROOT / "dashboard"


def _snapshot(root: Path) -> dict[str, bytes | None]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def _profile(root: Path, name: str = "alpha") -> Path:
    home = root if name == "default" else root / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    return home


def test_cli_dry_run_is_non_mutating_and_install_is_explicit(tmp_path, capsys):
    root = tmp_path / "hermes"
    _profile(root)
    source = tmp_path / "dashboard"
    shutil.copytree(DASHBOARD_SOURCE, source)
    target = root / "profiles" / "alpha" / "plugins" / "approvals" / "dashboard"
    before = _snapshot(root)

    result = dashboard_installer.main([
        "install", "--profile", "alpha", "--hermes-home", str(root),
        "--dashboard-source", str(source), "--dry-run",
    ])
    assert result == 0
    assert "Would install/update" in capsys.readouterr().out
    assert _snapshot(root) == before
    assert not target.exists()

    # A non-dry-run install action is the deliberate write operation.
    assert dashboard_installer.main([
        "install", "--profile", "alpha", "--hermes-home", str(root),
        "--dashboard-source", str(source),
    ]) == 0
    assert (target / "manifest.json").is_file()


def test_install_repeat_is_idempotent_and_uninstall_preserves_neighbors(tmp_path, capsys):
    root = tmp_path / "hermes"
    alpha = _profile(root, "alpha")
    beta = _profile(root, "beta")
    target = alpha / "plugins" / "approvals" / "dashboard"
    target.mkdir(parents=True)
    sibling_plugin_file = alpha / "plugins" / "other-plugin" / "manifest.json"
    sibling_plugin_file.parent.mkdir(parents=True)
    sibling_plugin_file.write_text('{"name":"other-plugin"}\n', encoding="utf-8")
    sibling_job = alpha / "cron" / "jobs.json"
    sibling_job.parent.mkdir(parents=True)
    sibling_job.write_text('{"jobs":[{"id":"sibling-job"}]}\n', encoding="utf-8")
    neighboring_plugin = beta / "plugins" / "neighbor" / "manifest.json"
    neighboring_plugin.parent.mkdir(parents=True)
    neighboring_plugin.write_text('{"name":"neighbor"}\n', encoding="utf-8")
    before_neighbor_state = _snapshot(beta)
    before_siblings = {sibling_plugin_file: sibling_plugin_file.read_bytes(), sibling_job: sibling_job.read_bytes()}
    source = tmp_path / "dashboard"
    shutil.copytree(DASHBOARD_SOURCE, source)

    args = ["install", "--profile", "alpha", "--hermes-home", str(root), "--dashboard-source", str(source)]
    assert dashboard_installer.main(args) == 0
    capsys.readouterr()
    installed = _snapshot(target)
    receipt = (target / dashboard_installer.OWNER_FILE).read_bytes()
    assert dashboard_installer.main(args) == 0
    assert _snapshot(target) == installed
    assert (target / dashboard_installer.OWNER_FILE).read_bytes() == receipt
    assert sibling_plugin_file.read_bytes() == before_siblings[sibling_plugin_file]
    assert sibling_job.read_bytes() == before_siblings[sibling_job]
    assert _snapshot(beta) == before_neighbor_state

    assert dashboard_installer.main([
        "uninstall", "--profile", "alpha", "--hermes-home", str(root), "--dry-run",
    ]) == 0
    capsys.readouterr()
    assert _snapshot(target) == installed

    assert dashboard_installer.main([
        "uninstall", "--profile", "alpha", "--hermes-home", str(root),
    ]) == 0
    message = capsys.readouterr().out
    assert "Removed only installer-owned dashboard files" in message
    assert target.is_dir()  # existed before installation, so rollback preserves it
    assert list(target.iterdir()) == []
    assert sibling_plugin_file.read_bytes() == before_siblings[sibling_plugin_file]
    assert sibling_job.read_bytes() == before_siblings[sibling_job]
    assert _snapshot(beta) == before_neighbor_state


def test_uninstall_preserves_locally_changed_and_unowned_files(tmp_path):
    root = tmp_path / "hermes"
    _profile(root)
    source = tmp_path / "dashboard"
    shutil.copytree(DASHBOARD_SOURCE, source)
    assert dashboard_installer.install(root, "default", source)
    target = root / "plugins" / "approvals" / "dashboard"
    changed_owned = target / "manifest.json"
    changed_owned.write_text('{"operator":"edited"}\n', encoding="utf-8")
    unowned = target / "operator-notes.txt"
    unowned.write_text("preserve\n", encoding="utf-8")

    message = dashboard_installer.uninstall(root, "default")
    assert "preserved 1 locally changed file" in message
    assert changed_owned.read_text(encoding="utf-8") == '{"operator":"edited"}\n'
    assert unowned.read_text(encoding="utf-8") == "preserve\n"
    assert not (target / "plugin_api.py").exists()
    assert not (target / dashboard_installer.OWNER_FILE).exists()


def test_installer_refuses_symlinked_payload_parent(tmp_path):
    root = tmp_path / "hermes"
    _profile(root)
    source = tmp_path / "dashboard"
    shutil.copytree(DASHBOARD_SOURCE, source)
    dashboard_installer.install(root, "default", source)
    target = root / "plugins" / "approvals" / "dashboard"
    outside = tmp_path / "outside"
    outside.mkdir()
    (target / "dist").rename(target / "dist-original")
    (target / "dist").symlink_to(outside, target_is_directory=True)

    with pytest.raises(dashboard_installer.InstallerError, match="symlinked package directory"):
        dashboard_installer.install(root, "default", source)
    with pytest.raises(dashboard_installer.InstallerError, match="symlinked package directory"):
        dashboard_installer.uninstall(root, "default")
    assert list(outside.iterdir()) == []
    assert (target / "dist-original" / "index.js").is_file()


def test_installer_refuses_unowned_payload_and_bad_profile(tmp_path):
    root = tmp_path / "hermes"
    alpha = _profile(root, "alpha")
    source = tmp_path / "dashboard"
    shutil.copytree(DASHBOARD_SOURCE, source)
    target = alpha / "plugins" / "approvals" / "dashboard"
    target.mkdir(parents=True)
    existing = target / "manifest.json"
    existing.write_text("operator version", encoding="utf-8")

    with pytest.raises(dashboard_installer.InstallerError, match="unowned"):
        dashboard_installer.install(root, "alpha", source)
    assert existing.read_text(encoding="utf-8") == "operator version"
    with pytest.raises(dashboard_installer.InstallerError, match="does not exist"):
        dashboard_installer.install(root, "missing", source)
    assert json.loads((source / "manifest.json").read_text(encoding="utf-8"))["name"] == "approvals"
