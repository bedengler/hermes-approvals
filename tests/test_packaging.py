from pathlib import Path
from zipfile import ZipFile
import subprocess


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
    assert {
        "dashboard/__init__.py",
        "dashboard/plugin_api.py",
        "dashboard/manifest.json",
        "dashboard/dist/index.js",
    } <= names