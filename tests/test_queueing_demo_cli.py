import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]


def _run_cli(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "experiments.queueing_demo", *map(str, arguments)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_module_cli_help_describes_app_configuration():
    result = _run_cli("--help")

    assert result.returncode == 0
    assert "--output-dir" in result.stdout
    assert "--run-id" in result.stdout
    assert "--workers" in result.stdout
    assert "--sampling-interval" in result.stdout


def test_existing_output_directory_returns_actionable_error_without_overwrite(tmp_path):
    output_dir = tmp_path / "existing-run"
    output_dir.mkdir()
    marker = output_dir / "preserve.txt"
    marker.write_text("keep", encoding="utf-8")

    result = _run_cli("--output-dir", output_dir, "--port", 0)

    assert result.returncode == 2
    assert "already exists" in result.stderr.lower()
    assert marker.read_text(encoding="utf-8") == "keep"
