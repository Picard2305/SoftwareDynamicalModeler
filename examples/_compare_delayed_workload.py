import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from sdmodeler.cli import _load_input_csv
from sdmodeler.config import PipelineConfig
from sdmodeler.data.segmentation import DeployBoundary, SegmentMetadata, assign_deploy_ids
from sdmodeler.identification.differentiation import differentiate
from sdmodeler.identification.feature_library import build_feature_library
from sdmodeler.identification.fitting import FittedModel, _r_squared, fit_mode_segment
from sdmodeler.identification.mode_switching import ModeSegment
from sdmodeler.identification.optimizer import build_optimizer
from sdmodeler.identification.plant_controller import build_plant_controller_data, prepare_frame_for_identification
from sdmodeler.validation import forecast_samples, normalized_rmse, validate_model


DELAY_SAMPLES = 3
VALIDATION_SAMPLES = 16
ARX_CANDIDATE = "arx_delayed"
CANDIDATES = {"current": ["Instances", "RequestRate"], "delayed": ["Instances_delay3", "RequestRate_delay3"]}


def _load_episodes(root, holdout_csv=None):
    examples = Path(root) / "examples"
    config = PipelineConfig.from_file(examples / "synthetic_config.json")
    frame = _load_input_csv(str(examples / "synthetic_train.csv"))
    boundary_data = json.loads((examples / "synthetic_boundaries.json").read_text(encoding="utf-8"))
    boundaries = [DeployBoundary(deploy_id=item["deploy_id"], start=pd.Timestamp(item["start"])) for item in boundary_data]
    frame = assign_deploy_ids(frame, boundaries)
    episodes = {name: part.drop(columns="deploy_id") for name, part in frame.groupby("deploy_id", sort=True)}
    holdout_path = Path(holdout_csv) if holdout_csv is not None else examples / "synthetic_holdout.csv"
    holdout = _load_input_csv(str(holdout_path))
    return config, episodes, holdout


def _add_delayed_inputs(frame):
    delayed = frame.copy()
    for name in ("Instances", "RequestRate"):
        delayed[f"{name}_delay{DELAY_SAMPLES}"] = delayed[name].shift(DELAY_SAMPLES).fillna(delayed[name].iloc[0])
    return delayed


def _candidate_config(config, input_names):
    candidate_config = config.model_copy(deep=True)
    candidate_config.plant_controller_split.u_variables = list(input_names)
    return candidate_config


def _metadata(frame, episode_name, sample_count):
    return SegmentMetadata(
        deploy_id=episode_name,
        regime_label="business_hours",
        start=frame.index[0],
        end=frame.index[sample_count - 1],
        sample_count=sample_count,
    )


def _fit_episode_candidates(frame, episode_name, config, input_names):
    split = len(frame) - VALIDATION_SAMPLES
    training_frame = frame.iloc[:split]
    validation_frame = frame.iloc[split:]
    candidate_config = _candidate_config(config, input_names)
    metadata = _metadata(training_frame, episode_name, len(training_frame))
    mode_segment = ModeSegment(
        mode_label="default",
        start=training_frame.index[0],
        end=training_frame.index[-1],
        sample_count=len(training_frame),
        frame=training_frame,
    )
    scored = []
    for model in fit_mode_segment(mode_segment, metadata, candidate_config):
        result = validate_model(model, validation_frame, candidate_config.feature_library, candidate_config.validation)
        if not result.applicable or result.predicted_trajectory is None or result.actual_trajectory is None:
            continue
        forecast_actual, forecast_predicted = forecast_samples(
            result.actual_trajectory, result.predicted_trajectory
        )
        mae = float(np.mean(np.abs(forecast_actual[:, 0] - forecast_predicted[:, 0])))
        scored.append({
            "model": model,
            "mae": mae,
            "normalized_rmse": float(result.score),
            "threshold": model.sparsity_threshold,
            "reliable": bool(result.is_reliable),
        })
    return scored


def select_candidate(training_metrics):
    candidates = sorted({item["candidate"] for item in training_metrics})
    if not candidates:
        raise ValueError("No training candidate metrics were provided.")
    means = {
        candidate: (
            float(np.mean([item["normalized_rmse"] for item in training_metrics if item["candidate"] == candidate])),
            float(np.mean([item["mae"] for item in training_metrics if item["candidate"] == candidate])),
        )
        for candidate in candidates
    }
    return min(means, key=lambda candidate: (means[candidate][0], means[candidate][1], candidate))


def _fit_arx_model(frames):
    feature_rows = []
    target_rows = []
    delayed_names = CANDIDATES["delayed"]
    for frame in frames:
        delayed = _add_delayed_inputs(frame)
        states = delayed["CPU"].to_numpy(dtype=float)
        controls = delayed[delayed_names].to_numpy(dtype=float)
        feature_rows.append(np.column_stack([states[:-1], controls[1:]]))
        target_rows.append(states[1:])
    model = LinearRegression()
    model.fit(np.vstack(feature_rows), np.concatenate(target_rows))
    return model


def _simulate_arx(model, frame):
    window = frame.iloc[-VALIDATION_SAMPLES:]
    delayed = _add_delayed_inputs(frame).iloc[-VALIDATION_SAMPLES:]
    actual = window[["CPU"]].to_numpy(dtype=float)
    predicted = np.empty_like(actual)
    predicted[0] = actual[0]
    for index in range(1, len(window)):
        controls = delayed.iloc[index][CANDIDATES["delayed"]].to_numpy(dtype=float)
        features = np.concatenate([predicted[index - 1], controls]).reshape(1, -1)
        predicted[index, 0] = np.clip(model.predict(features)[0], 0.0, 100.0)
    return window.index, actual, predicted


def _score_arx_episode(frame, acceptance_threshold):
    split = len(frame) - VALIDATION_SAMPLES
    model = _fit_arx_model([frame.iloc[:split]])
    timestamps, actual, predicted = _simulate_arx(model, frame)
    forecast_actual, forecast_predicted = forecast_samples(actual, predicted)
    score = normalized_rmse(forecast_actual, forecast_predicted)
    mae = float(np.mean(np.abs(forecast_actual[:, 0] - forecast_predicted[:, 0])))
    return {
        "mae": mae,
        "normalized_rmse": score,
        "threshold": None,
        "reliable": score <= acceptance_threshold,
    }


def _fit_pooled_model(episodes, candidate_name, config, input_names, threshold):
    candidate_config = _candidate_config(config, input_names)
    feature_rows = []
    derivative_rows = []
    library_result = None
    for episode in episodes.values():
        frame = _add_delayed_inputs(episode) if candidate_name == "delayed" else episode.copy()
        frame = prepare_frame_for_identification(frame, candidate_config.plant_controller_split)
        plant_data = build_plant_controller_data(frame, candidate_config.plant_controller_split)
        library_result = build_feature_library(
            frame,
            plant_data.x_variables,
            candidate_config.feature_library,
            additional_features=plant_data.u,
        )
        design = np.hstack([library_result.features.to_numpy(dtype=float), plant_data.u])
        times = (frame.index - frame.index[0]).total_seconds().to_numpy()
        derivative = differentiate(plant_data.x, times, candidate_config.differentiation)
        feature_rows.append(design)
        derivative_rows.append(derivative.dx)
    theta = np.vstack(feature_rows)
    dx = np.vstack(derivative_rows)
    optimizer = build_optimizer(candidate_config.optimizer, threshold)
    optimizer.fit(theta, dx)
    coefficients = np.asarray(optimizer.coef_)
    feature_names = list(library_result.features.columns) + [f"u:{name}" for name in input_names]
    first = next(iter(episodes.values()))
    last = list(episodes.values())[-1]
    metadata = SegmentMetadata(
        deploy_id="training_episodes",
        regime_label="business_hours",
        start=first.index[0],
        end=last.index[-1],
        sample_count=sum(len(frame) for frame in episodes.values()),
    )
    return FittedModel(
        coefficients=coefficients,
        feature_names=feature_names,
        x_variables=list(candidate_config.plant_controller_split.x_variables),
        u_variables=list(input_names),
        library_tiers=list(candidate_config.feature_library.enabled_tiers),
        condition_number=library_result.condition_number,
        condition_number_warning=library_result.condition_number_warning,
        differentiation_method=candidate_config.differentiation.method.value,
        differentiation_params={},
        optimizer_method=candidate_config.optimizer.method.value,
        sparsity_threshold=threshold,
        n_nonzero_terms=int(np.count_nonzero(coefficients)),
        segment_metadata=metadata,
        mode_label="default",
        sample_count=metadata.sample_count,
        r_squared=_r_squared(dx, theta @ coefficients.T),
        feature_library_config=candidate_config.feature_library.model_copy(deep=True),
    )


def run_experiment(root, holdout_csv=None):
    config, episodes, holdout = _load_episodes(root, holdout_csv)
    training_metrics = []
    threshold_choices = []
    for episode_name, episode in episodes.items():
        for candidate_name, input_names in CANDIDATES.items():
            candidate_frame = _add_delayed_inputs(episode) if candidate_name == "delayed" else episode.copy()
            scored = _fit_episode_candidates(candidate_frame, episode_name, config, input_names)
            if not scored:
                raise RuntimeError(f"No applicable validation result for {candidate_name} on {episode_name}.")
            best = min(scored, key=lambda item: (item["normalized_rmse"], item["mae"], item["threshold"]))
            threshold_choices.append((candidate_name, best["threshold"]))
            training_metrics.append({
                "candidate": candidate_name,
                "episode": episode_name,
                "mae": best["mae"],
                "normalized_rmse": best["normalized_rmse"],
                "threshold": best["threshold"],
                "reliable": best["reliable"],
            })
        arx_score = _score_arx_episode(episode, config.validation.acceptance_threshold)
        training_metrics.append({"candidate": ARX_CANDIDATE, "episode": episode_name, **arx_score})
    selected = select_candidate(training_metrics)
    selected_threshold = None
    if selected == ARX_CANDIDATE:
        selected_model = _fit_arx_model(episodes.values())
        timestamps, actual, predicted = _simulate_arx(selected_model, holdout)
        forecast_actual, forecast_predicted = forecast_samples(actual, predicted)
        validation_score = normalized_rmse(forecast_actual, forecast_predicted)
        validation_reliable = validation_score <= config.validation.acceptance_threshold
    else:
        selected_thresholds = [threshold for candidate, threshold in threshold_choices if candidate == selected]
        threshold_counts = Counter(selected_thresholds)
        selected_threshold = min(
            (threshold for threshold, count in threshold_counts.items() if count == max(threshold_counts.values()))
        )
        selected_model = _fit_pooled_model(episodes, selected, config, CANDIDATES[selected], selected_threshold)
        holdout_frame = _add_delayed_inputs(holdout) if selected == "delayed" else holdout
        validation = validate_model(selected_model, holdout_frame, config.feature_library, config.validation)
        if not validation.applicable or validation.predicted_trajectory is None or validation.actual_trajectory is None:
            raise RuntimeError("The reserved holdout did not produce an applicable validation result.")
        timestamps = validation.timestamps
        actual = validation.actual_trajectory
        predicted = validation.predicted_trajectory
        forecast_actual, forecast_predicted = forecast_samples(actual, predicted)
        validation_score = float(validation.score)
        validation_reliable = bool(validation.is_reliable)
    holdout_mae = float(np.mean(np.abs(forecast_actual[:, 0] - forecast_predicted[:, 0])))
    holdout_metrics = [{
        "candidate": selected,
        "mae": holdout_mae,
        "normalized_rmse": validation_score,
        "reliable": validation_reliable,
        "threshold": selected_threshold,
    }]
    holdout_diagnostics = [
        {
            "timestamp": pd.Timestamp(timestamp).isoformat(),
            "observed_cpu": float(actual[index, 0]),
            "simulated_cpu": float(predicted[index, 0]),
            "residual_cpu": float(actual[index, 0] - predicted[index, 0]),
        }
        for index, timestamp in enumerate(timestamps)
    ]
    return {
        "selection_rule": "Select the candidate with the lower unweighted mean episode normalized RMSE; use mean absolute CPU error as the first tie-breaker. The SINDy candidates select a threshold per episode by normalized RMSE, then MAE, then the lower threshold; the final SINDy fit uses the most frequent selected threshold, breaking ties toward the lower threshold. The delayed ARX candidate has no sparsity threshold.",
        "training_metrics": training_metrics,
        "selected_candidate": selected,
        "selected_threshold": selected_threshold,
        "holdout_metrics": holdout_metrics,
        "holdout_diagnostics": holdout_diagnostics,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout-csv", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(run_experiment(root, args.holdout_csv), indent=2))


if __name__ == "__main__":
    main()
