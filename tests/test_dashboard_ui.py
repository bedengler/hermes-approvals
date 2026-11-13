from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
BUNDLE = ROOT / "dashboard" / "dist" / "index.js"
CONTRACT = Path(__file__).with_name("dashboard_ui_contract.cjs")


@pytest.mark.parametrize("mode", ["success", "fail"])
def test_dashboard_renders_read_only_pending_and_history_views(mode):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the shipped dashboard bundle")
    result = subprocess.run(
        [node, str(CONTRACT), str(BUNDLE), "fail" if mode == "fail" else "success"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
