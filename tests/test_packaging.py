import json
import subprocess
from pathlib import Path
from zipfile import ZipFile


def test_wheel_contains_documented_dashboard_payload(tmp_path: Path):
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        check=True,
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
    )
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
        manifest = json.loads(wheel.read("dashboard/manifest.json"))
        bundled_ui = wheel.read("dashboard/dist/index.js")
        dashboard_adapter = wheel.read("dashboard/plugin_api.py")
        reusable_api = wheel.read("hermes_approval/api.py")
    assert {
        "hermes_approval/dashboard_installer.py",
        "dashboard/__init__.py",
        "dashboard/approval_cleanup.py",
        "dashboard/plugin_api.py",
        "dashboard/manifest.json",
        "dashboard/dist/index.js",
    } <= names
    # Hermes only mounts backend routes when the manifest advertises the
    # module-level adapter. Keep the static dashboard/API contract together.
    assert manifest["api"] == "plugin_api.py"
    assert bundled_ui == (Path(__file__).parents[1] / "dashboard" / "dist" / "index.js").read_bytes()
    assert dashboard_adapter == (Path(__file__).parents[1] / "dashboard" / "plugin_api.py").read_bytes()
    assert reusable_api == (Path(__file__).parents[1] / "src" / "hermes_approval" / "api.py").read_bytes()
