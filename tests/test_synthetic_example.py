import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from sdmodeler.cli import main
from sdmodeler.config import PipelineConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ("synthetic_train.csv", "synthetic_holdout.csv", "synthetic_config.json", "synthetic_boundaries.json")


def generate(output_dir, seed=2026):
    subprocess.run(
        [sys.executable, "-m", "examples._generate_synthetic", "--output-dir", str(output_dir), "--seed", str(seed)],
        check=True,
        cwd=PROJECT_ROOT,
    )


def test_generator_is_deterministic_and_bundled_data_matches(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate(first)
    generate(second)
    for name in ARTIFACTS:
        assert (first / name).read_bytes() == (second / name).read_bytes()
        assert (first / name).read_bytes() == (PROJECT_ROOT / "examples" / name).read_bytes()


def test_generator_reproduces_a_seed_and_changes_telemetry_for_another_seed(tmp_path):
    first = tmp_path / "first"
    repeated = tmp_path / "repeated"
    independent = tmp_path / "independent"
    generate(first, seed=31415)
    generate(repeated, seed=31415)
    generate(independent, seed=27182)

    first_train = pd.read_csv(first / "synthetic_train.csv")
    repeated_train = pd.read_csv(repeated / "synthetic_train.csv")
    independent_train = pd.read_csv(independent / "synthetic_train.csv")
    pd.testing.assert_frame_equal(first_train, repeated_train)
    assert not first_train.equals(independent_train)


def test_synthetic_traces_have_independent_inputs_bounded_cpu_and_disjoint_holdout(tmp_path):
    generate(tmp_path)
    train = pd.read_csv(tmp_path / "synthetic_train.csv", parse_dates=["timestamp"])
    holdout = pd.read_csv(tmp_path / "synthetic_holdout.csv", parse_dates=["timestamp"])
    config = PipelineConfig.from_file(tmp_path / "synthetic_config.json")
    boundaries = json.loads((tmp_path / "synthetic_boundaries.json").read_text(encoding="utf-8"))

    assert set(config.plant_controller_split.x_variables) == {"CPU"}
    assert set(config.plant_controller_split.u_variables) == {"Instances", "RequestRate"}
    assert len(boundaries) > 1
    assert all(train["timestamp"].min() <= pd.Timestamp(boundary["start"]) <= train["timestamp"].max() for boundary in boundaries)
    assert set(train["timestamp"]).isdisjoint(holdout["timestamp"])
    for frame in (train, holdout):
        assert len(frame) > config.segmentation.min_segment_length_samples
        assert frame["timestamp"].is_monotonic_increasing
        assert frame["timestamp"].is_unique
        assert frame["timestamp"].dt.tz is not None
        assert np.isfinite(frame[["CPU", "Instances", "RequestRate"]].to_numpy()).all()
        assert frame["CPU"].between(0, 100).all()
        assert (frame["Instances"] >= 1).all()
        assert (frame["RequestRate"] >= 0).all()
        assert frame["Instances"].nunique() > 1
        assert frame["RequestRate"].nunique() > 1
        assert frame["CPU"].nunique() > 1
        assert not frame["Instances"].equals(frame["RequestRate"])


def test_synthetic_training_data_runs_through_cli(tmp_path, monkeypatch):
    generate(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main([
        "run", "--config", str(tmp_path / "synthetic_config.json"),
        "--input-csv", str(tmp_path / "synthetic_train.csv"),
        "--deploy-boundaries", str(tmp_path / "synthetic_boundaries.json"),
        "--allow-unresolved-design-questions", "--include-unreliable",
        "--output", str(tmp_path / "models.json"),
    ]) == 0
    models = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))["models"]
    assert models
    holdout = pd.read_csv(tmp_path / "synthetic_holdout.csv", parse_dates=["timestamp"])
    boundaries = json.loads((tmp_path / "synthetic_boundaries.json").read_text(encoding="utf-8"))
    assert {model["segment_metadata"]["deploy_id"] for model in models} == {
        boundary["deploy_id"] for boundary in boundaries
    }
    assert all(pd.Timestamp(model["segment_metadata"]["end"]) < holdout["timestamp"].min() for model in models)
    assert (tmp_path / "ise_robustness_threshold.png").exists()
