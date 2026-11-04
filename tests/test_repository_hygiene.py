import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IGNORED_GENERATED_PATHS = (
    "runs/__pycache__/generated.cpython-314.pyc",
    ".venv/Scripts/python.exe",
    ".pytest_cache/v/cache/lastfailed",
)


def test_generated_python_and_environment_artifacts_are_ignored():
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *IGNORED_GENERATED_PATHS],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert set(result.stdout.splitlines()) == set(IGNORED_GENERATED_PATHS)
