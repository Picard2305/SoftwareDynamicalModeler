import json
import subprocess
import sys
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYZER = PROJECT_ROOT / "runs" / "analyze_queue_new_arrival_study.py"
REFERENCE_RESULTS = PROJECT_ROOT / "runs" / "queue-new-arrival-profile-study-002.json"


def test_importing_analysis_module_does_not_parse_arguments_or_run_analysis():
    import_code = (
        "import importlib.util, sys; "
        "path = sys.argv[1]; "
        "sys.argv = ['analysis', '--unknown-option']; "
        "spec = importlib.util.spec_from_file_location('analysis_under_test', path); "
        "module = importlib.util.module_from_spec(spec); "
        "spec.loader.exec_module(module)"
    )

    result = subprocess.run(
        [sys.executable, "-c", import_code, str(ANALYZER)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_analysis_cli_reproduces_metrics_without_overwriting_reference(tmp_path):
    reference_before = REFERENCE_RESULTS.read_bytes()
    output_path = tmp_path / "reproduced-results.json"

    result = subprocess.run(
        [sys.executable, str(ANALYZER), "--output", str(output_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, result.stderr
    assert output_path.is_file()
    assert REFERENCE_RESULTS.read_bytes() == reference_before

    expected = json.loads(reference_before)
    actual = json.loads(output_path.read_text(encoding="utf-8"))
    assert actual["held_out_run_ids"] == expected["held_out_run_ids"]
    assert actual["training_run_ids"] == expected["training_run_ids"]
    provenance = actual["reproducibility"]
    assert provenance["repository_commit"]
    assert provenance["sdmodeler_commit"]
    assert provenance["python_version"]
    assert set(provenance["package_versions"]) == {
        "numpy",
        "pandas",
        "pysindy",
        "scikit-learn",
        "pydantic",
    }
    assert all(provenance["package_versions"].values())
    assert set(provenance["k6_versions_by_run"]) == set(
        expected["training_run_ids"] + expected["held_out_run_ids"]
    )
    assert all(
        version is None or isinstance(version, str)
        for version in provenance["k6_versions_by_run"].values()
    )
    assert actual["aggregate_by_profile"].keys() == expected["aggregate_by_profile"].keys()

    for profile, methods in expected["aggregate_by_profile"].items():
        assert actual["aggregate_by_profile"][profile].keys() == methods.keys()
        for method, metrics in methods.items():
            for metric, summary in metrics.items():
                actual_summary = actual["aggregate_by_profile"][profile][method][metric]
                assert actual_summary.keys() == summary.keys()
                np.testing.assert_allclose(
                    [actual_summary[statistic] for statistic in summary],
                    [summary[statistic] for statistic in summary],
                    rtol=1e-10,
                    atol=1e-10,
                )
