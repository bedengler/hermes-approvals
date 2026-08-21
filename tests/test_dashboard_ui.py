from pathlib import Path


ROOT = Path(__file__).parents[1]
BUNDLE = ROOT / "dashboard" / "dist" / "index.js"


def test_dashboard_has_refresh_feedback_and_safe_empty_state():
    source = BUNDLE.read_text(encoding="utf-8")
    assert "setRefreshing(true)" in source
    assert "disabled: refreshing" in source
    assert "No changes; nothing pending." in source
    assert "Refresh failed. Please try again." in source
    assert "Last refreshed" in source


def test_governance_decisions_require_explicit_second_confirmation():
    source = BUNDLE.read_text(encoding="utf-8")
    for field in ("approval_id", "gate", "target", "expires_at", "rationale"):
        assert f"confirming.item.{field}" in source
    assert "Confirm final" in source
    assert "aria-modal" in source
    assert '"data-modal": "governance-confirmation"' in source

    assert "bg-(--ui-bg)/80" in source
    assert "backdrop-blur-sm" in source
    assert "bg-(--ui-surface-background)" in source
    assert "border-(--ui-stroke-secondary)" in source
    assert "shadow-lg" in source
    assert "text-(--ui-text-primary)" in source
    assert "text-(--ui-text-secondary)" in source
    assert "onKeyDown" in source
    assert 'event.key === "Escape"' in source
    assert "decisionOutcome" in source


def test_interactive_controls_have_keyboard_and_pointer_affordances():
    source = BUNDLE.read_text(encoding="utf-8")
    assert source.count("className: primary") >= 3
    assert source.count("className: secondary") >= 3
    assert "cursor-pointer" in source
    assert "focus-visible:outline" in source
    assert "disabled:cursor-not-allowed" in source


def test_installed_bundle_matches_repository_bundle():
    installed = Path.home() / ".hermes" / "plugins" / "approvals" / "dashboard" / "dist" / "index.js"
    if installed.exists():
        assert installed.read_text(encoding="utf-8") == BUNDLE.read_text(encoding="utf-8")
