from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_SOURCE = PROJECT_ROOT / "dashboard"


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): None if path.is_dir() else path.read_bytes()
        for path in sorted(root.rglob("*"))
    }


def _cli(
    root: Path, profile: str, action: str, source: Path = DASHBOARD_SOURCE, *flags: str
):
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            (
                str(PROJECT_ROOT / "src"),
                str(PROJECT_ROOT),
                os.environ.get("PYTHONPATH", ""),
            )
        ),
        "HERMES_DISABLE_LAZY_INSTALLS": "1",
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_approval.dashboard_installer",
            action,
            "--profile",
            profile,
            "--hermes-home",
            str(root),
            "--source",
            str(source),
            *flags,
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _make_source(tmp_path: Path, version: str) -> Path:
    source = tmp_path / f"dashboard-{version}"
    shutil.copytree(DASHBOARD_SOURCE, source)
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return source


def test_cli_dry_run_does_not_create_or_change_the_explicit_hermes_home(tmp_path):
    home = tmp_path / "disposable-hermes-home"

    result = _cli(home, "alpha", "install", DASHBOARD_SOURCE, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "Would install" in result.stdout
    assert not home.exists()

    profile = home / "profiles" / "alpha"
    profile.mkdir(parents=True)
    (profile / "keep.txt").write_text("existing profile data\n", encoding="utf-8")
    before = _tree_snapshot(home)
    existing_home_result = _cli(home, "alpha", "install", DASHBOARD_SOURCE, "--dry-run")

    assert existing_home_result.returncode == 0, existing_home_result.stderr
    assert "Would install" in existing_home_result.stdout
    assert _tree_snapshot(home) == before


def test_cli_default_profile_uses_the_explicit_hermes_home_root(tmp_path):
    home = tmp_path / "disposable-hermes-home"

    installed = _cli(home, "default", "install")

    assert installed.returncode == 0, installed.stderr
    target = home / "plugins" / "approvals" / "dashboard"
    assert (target / "manifest.json").is_file()
    assert not (home / "profiles" / "default" / "plugins").exists()

    rolled_back = _cli(home, "default", "rollback")

    assert rolled_back.returncode == 0, rolled_back.stderr
    assert not target.exists()


def test_cli_installs_upgrades_and_rolls_back_only_the_selected_profile(tmp_path):
    home = tmp_path / "disposable-hermes-home"
    alpha = home / "profiles" / "alpha"
    beta = home / "profiles" / "beta"
    target = alpha / "plugins" / "approvals" / "dashboard"
    sibling = alpha / "plugins" / "sibling" / "dashboard"
    beta_plugin = beta / "plugins" / "neighbor" / "dashboard"

    for plugin, name in ((sibling, "sibling"), (beta_plugin, "neighbor")):
        plugin.mkdir(parents=True)
        (plugin / "manifest.json").write_text(
            json.dumps({"name": name, "version": "7"}), encoding="utf-8"
        )
        (plugin / "keep.txt").write_text(f"{name} stays\n", encoding="utf-8")
    (alpha / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    sibling_before = _tree_snapshot(alpha / "plugins" / "sibling")
    beta_before = _tree_snapshot(beta)
    config_before = (alpha / "config.yaml").read_bytes()

    first = _cli(home, "alpha", "install")
    assert first.returncode == 0, first.stderr
    assert "Installed" in first.stdout
    initial_manifest = json.loads(
        (target / "manifest.json").read_text(encoding="utf-8")
    )
    assert initial_manifest["name"] == "approvals"
    assert _tree_snapshot(alpha / "plugins" / "sibling") == sibling_before
    assert _tree_snapshot(beta) == beta_before
    assert (alpha / "config.yaml").read_bytes() == config_before

    installed_before_repeat = _tree_snapshot(alpha)
    repeat = _cli(home, "alpha", "install")
    assert repeat.returncode == 0, repeat.stderr
    assert "Already installed" in repeat.stdout
    assert _tree_snapshot(alpha) == installed_before_repeat

    v2 = _make_source(tmp_path, "9.9.9-test")
    upgrade = _cli(home, "alpha", "install", v2)
    assert upgrade.returncode == 0, upgrade.stderr
    upgraded_manifest = json.loads(
        (target / "manifest.json").read_text(encoding="utf-8")
    )
    assert upgraded_manifest["version"] == "9.9.9-test"

    rollback_upgrade = _cli(home, "alpha", "rollback")
    assert rollback_upgrade.returncode == 0, rollback_upgrade.stderr
    restored_manifest = json.loads(
        (target / "manifest.json").read_text(encoding="utf-8")
    )
    assert restored_manifest["version"] == initial_manifest["version"]

    rollback_initial_install = _cli(home, "alpha", "rollback")
    assert rollback_initial_install.returncode == 0, rollback_initial_install.stderr
    assert not target.exists()
    assert _tree_snapshot(alpha / "plugins" / "sibling") == sibling_before
    assert _tree_snapshot(beta) == beta_before
    assert (alpha / "config.yaml").read_bytes() == config_before


def test_cli_refuses_to_replace_an_unrelated_plugin_and_preserves_it(tmp_path):
    home = tmp_path / "disposable-hermes-home"
    target = home / "profiles" / "alpha" / "plugins" / "approvals" / "dashboard"
    target.mkdir(parents=True)
    foreign = {"name": "not-approvals", "entry": "custom.js"}
    (target / "manifest.json").write_text(json.dumps(foreign), encoding="utf-8")
    before = _tree_snapshot(target)

    result = _cli(home, "alpha", "install")

    assert result.returncode != 0
    assert "refusing" in result.stderr.lower()
    assert _tree_snapshot(target) == before


def test_cli_rollback_refuses_if_the_installed_payload_was_edited(tmp_path):
    home = tmp_path / "disposable-hermes-home"
    result = _cli(home, "alpha", "install")
    assert result.returncode == 0, result.stderr
    target = home / "profiles" / "alpha" / "plugins" / "approvals" / "dashboard"
    (target / "operator-edit.txt").write_text("do not discard\n", encoding="utf-8")

    rollback = _cli(home, "alpha", "rollback")

    assert rollback.returncode != 0
    assert "changed since" in rollback.stderr.lower()
    assert (target / "operator-edit.txt").read_text(
        encoding="utf-8"
    ) == "do not discard\n"


def test_cli_preserves_and_restores_preexisting_files_inside_its_plugin(tmp_path):
    home = tmp_path / "disposable-hermes-home"
    target = home / "profiles" / "alpha" / "plugins" / "approvals" / "dashboard"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(
        json.dumps({"name": "approvals", "version": "operator-installed"}),
        encoding="utf-8",
    )
    (target / "local-note.txt").write_text("operator content\n", encoding="utf-8")
    before = _tree_snapshot(target)

    installed = _cli(home, "alpha", "install")
    assert installed.returncode == 0, installed.stderr
    assert (target / "local-note.txt").read_text(
        encoding="utf-8"
    ) == "operator content\n"
    assert (target / "plugin_api.py").is_file()

    rolled_back = _cli(home, "alpha", "rollback")

    assert rolled_back.returncode == 0, rolled_back.stderr
    assert _tree_snapshot(target) == before


def test_cli_rejects_symlinked_profile_ancestors(tmp_path):
    home = tmp_path / "disposable-hermes-home"
    outside = tmp_path / "outside"
    outside.mkdir()
    home.mkdir()
    (home / "profiles").symlink_to(outside, target_is_directory=True)

    result = _cli(home, "alpha", "install")

    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()
    assert list(outside.iterdir()) == []
