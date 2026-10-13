from pathlib import Path

import numpy as np
import pandas as pd

from examples._compare_delayed_workload import _load_episodes, _simulate_arx, run_experiment, select_candidate


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_episode_loader_uses_an_explicit_external_holdout_without_changing_training(tmp_path):
    source = pd.read_csv(PROJECT_ROOT / "examples" / "synthetic_holdout.csv", parse_dates=["timestamp"])
    source["timestamp"] = source["timestamp"] + pd.Timedelta(days=30)
    supplied_path = tmp_path / "external_holdout.csv"
    source.to_csv(supplied_path, index=False)
    expected = source.set_index("timestamp").sort_index()

    _, episodes, holdout = _load_episodes(PROJECT_ROOT, supplied_path)
    _, bundled_episodes, _ = _load_episodes(PROJECT_ROOT)

    pd.testing.assert_frame_equal(holdout, expected)
    assert episodes.keys() == bundled_episodes.keys()
    for name in episodes:
        pd.testing.assert_frame_equal(episodes[name], bundled_episodes[name])


def test_arx_rollout_responds_to_input_aligned_with_the_destination_sample():
    class RequestRatePredictor:
        def predict(self, features):
            return features[:, -1]

    timestamps = pd.date_range("2024-01-01", periods=40, freq="min", tz="UTC")
    baseline = pd.DataFrame(
        {"CPU": 20.0, "Instances": 2.0, "RequestRate": 10.0}, index=timestamps
    )
    changed = baseline.copy()
    changed.iloc[22, changed.columns.get_loc("RequestRate")] = 20.0

    _, _, baseline_prediction = _simulate_arx(RequestRatePredictor(), baseline)
    _, _, changed_prediction = _simulate_arx(RequestRatePredictor(), changed)

    assert changed_prediction[0, 0] == baseline_prediction[0, 0]
    assert changed_prediction[1, 0] > baseline_prediction[1, 0]


def test_arx_episode_metrics_ignore_initialized_prediction(monkeypatch):
    import examples._compare_delayed_workload as experiment

    timestamps = pd.date_range("2024-01-01", periods=20, freq="min", tz="UTC")
    frame = pd.DataFrame(
        {"CPU": np.linspace(10.0, 30.0, len(timestamps)), "Instances": 2.0, "RequestRate": 10.0},
        index=timestamps,
    )
    actual = frame[["CPU"]].to_numpy(dtype=float)[-16:]
    first_prediction = actual.copy()
    first_prediction[1:] += 3.0
    second_prediction = first_prediction.copy()
    second_prediction[0] += 100.0
    predictions = [first_prediction, second_prediction]
    monkeypatch.setattr(experiment, "_fit_arx_model", lambda frames: None)
    monkeypatch.setattr(
        experiment,
        "_simulate_arx",
        lambda model, source: (source.index[-16:], actual, predictions.pop(0)),
    )

    first_result = experiment._score_arx_episode(frame, 0.05)
    second_result = experiment._score_arx_episode(frame, 0.05)

    assert first_result["normalized_rmse"] == second_result["normalized_rmse"]
    assert first_result["mae"] == second_result["mae"]


def test_candidate_selection_is_invariant_to_training_episode_order():
    episode_metrics = [
        {"candidate": "current", "episode": "episode_1", "mae": 2.0, "normalized_rmse": 0.3},
        {"candidate": "delayed", "episode": "episode_1", "mae": 1.8, "normalized_rmse": 0.28},
        {"candidate": "current", "episode": "episode_2", "mae": 2.1, "normalized_rmse": 0.4},
        {"candidate": "delayed", "episode": "episode_2", "mae": 1.9, "normalized_rmse": 0.35},
    ]

    assert select_candidate(episode_metrics) == select_candidate(list(reversed(episode_metrics)))
    dominant_candidate = {
        "candidate": "arx_delayed",
        "episode": "episode_1",
        "mae": 0.1,
        "normalized_rmse": 0.05,
    }
    assert select_candidate(episode_metrics + [dominant_candidate]) == "arx_delayed"


def test_experiment_compares_all_training_episodes_then_only_selected_candidate_on_holdout():
    result = run_experiment(PROJECT_ROOT)

    assert len(result["training_metrics"]) == 9
    assert {item["episode"] for item in result["training_metrics"]} == {
        "episode_1", "episode_2", "episode_3"
    }
    assert {item["candidate"] for item in result["training_metrics"]} == {
        "current", "delayed", "arx_delayed"
    }
    assert len(result["holdout_metrics"]) == 1
    assert result["holdout_metrics"][0]["candidate"] == result["selected_candidate"]
    assert result["selected_candidate"] in {"current", "delayed", "arx_delayed"}
    diagnostics = result["holdout_diagnostics"]
    assert len(diagnostics) == 16
    assert [item["timestamp"] for item in diagnostics] == sorted(item["timestamp"] for item in diagnostics)
    assert all(
        np.isfinite(item[field])
        for item in diagnostics
        for field in ("observed_cpu", "simulated_cpu", "residual_cpu")
    )
    assert all(
        np.isfinite(item[metric]) and item[metric] >= 0
        for item in result["training_metrics"] + result["holdout_metrics"]
        for metric in ("mae", "normalized_rmse")
    )
