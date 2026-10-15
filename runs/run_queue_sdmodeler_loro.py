import csv
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

project_root = Path(r"C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler")
run_ids = [
    "queue-demo-service-time-55ms-profile-002",
    "queue-demo-service-time-55ms-profile-003",
    "queue-demo-service-time-55ms-profile-004",
]
sample_times = np.arange(0.0, 91.0, 1.0)
service_capacity_per_second = 1.0 / 0.055
sparsity_threshold = 0.001

import sys

sys.path.insert(0, str(project_root))

from sdmodeler.config import DifferentiationMethod, FeatureLibraryTier, PipelineConfig
from sdmodeler.identification.differentiation import differentiate
from sdmodeler.identification.feature_library import build_feature_library, build_feature_row
from sdmodeler.identification.optimizer import build_optimizer
from sdmodeler.identification.plant_controller import build_plant_controller_data
from sdmodeler.validation import normalized_rmse, simulate_forward

config = PipelineConfig.from_file(project_root / "examples" / "synthetic_config.json")
config.plant_controller_split.x_variables = ["QueueDepth"]
config.plant_controller_split.u_variables = ["ArrivalRate"]
config.feature_library.enabled_tiers = [FeatureLibraryTier.TIER_1_LINEAR_DOMAIN]
config.feature_library.tier1.domain_terms = ["1"]
config.differentiation.method = DifferentiationMethod.SAVITZKY_GOLAY
config.differentiation.savgol_window_length = 7
config.differentiation.savgol_polyorder = 2
config.optimizer.sparsity_threshold_grid = [sparsity_threshold]


def parse_utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_run(run_id):
    app_dir = project_root / "runs" / run_id / "app"
    with (app_dir / "jobs.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    parsed = [
        {
            "submitted": parse_utc(row["submitted_at"]).timestamp(),
            "accepted": parse_utc(row["accepted_at_utc"]).timestamp(),
            "completed": parse_utc(row["completed_at_utc"]).timestamp(),
        }
        for row in rows
        if row["accepted_at_utc"] and row["completed_at_utc"]
    ]
    load_start = min(job["submitted"] for job in parsed)
    parsed.sort(key=lambda job: job["accepted"])
    previous_completion = float("-inf")
    waiting_intervals = []
    service_times = []
    for job in parsed:
        service_start = max(job["accepted"], previous_completion)
        if service_start > job["accepted"]:
            waiting_intervals.append((job["accepted"] - load_start, service_start - load_start))
        service_times.append(job["completed"] - service_start)
        previous_completion = job["completed"]

    with (app_dir / "run_metadata.json").open(encoding="utf-8") as stream:
        metadata = json.load(stream)
    if metadata["worker_count"] != 1 or metadata["accepted_total"] != 1200:
        raise ValueError(f"Unexpected run metadata in {run_id}: {metadata}")

    queue_depth = np.array(
        [sum(start <= t < end for start, end in waiting_intervals) for t in sample_times],
        dtype=float,
    )
    arrival_rate = np.select(
        [sample_times < 30.0, sample_times < 60.0, sample_times <= 90.0],
        [10.0, 20.0, 10.0],
        default=0.0,
    )
    origin = pd.Timestamp.fromtimestamp(load_start, tz="UTC")
    frame = pd.DataFrame(
        {"QueueDepth": queue_depth, "ArrivalRate": arrival_rate},
        index=pd.date_range(origin, periods=len(sample_times), freq="1s"),
    )
    return frame, service_times


def make_training_arrays(frames):
    feature_blocks = []
    derivative_blocks = []
    for frame in frames:
        plant_data = build_plant_controller_data(frame, config.plant_controller_split)
        library = build_feature_library(
            frame,
            plant_data.x_variables,
            config.feature_library,
            additional_features=plant_data.u,
        )
        design = np.hstack([library.features.to_numpy(dtype=float), plant_data.u])
        times = np.arange(len(frame), dtype=float)
        derivative = differentiate(plant_data.x, times, config.differentiation).dx
        feature_blocks.append(design)
        derivative_blocks.append(derivative)
    return np.vstack(feature_blocks), np.vstack(derivative_blocks)


def evaluate(model, actual, arrival_rate, training_service_capacity):
    unbounded = simulate_forward(
        model,
        np.array([actual[0]], dtype=float),
        arrival_rate.reshape(-1, 1),
        1.0,
        config.feature_library,
    )[:, 0]

    projected = np.zeros_like(actual)
    projected[0] = actual[0]

    def derivative(queue_depth, rate):
        row = build_feature_row(
            np.array([queue_depth], dtype=float),
            np.array([rate], dtype=float),
            model.x_variables,
            model.u_variables,
            config.feature_library,
        )
        return float((row @ model.coefficients.T)[0])

    for index in range(1, len(actual)):
        previous = projected[index - 1]
        rate = arrival_rate[index]
        k1 = derivative(previous, rate)
        k2 = derivative(max(0.0, previous + 0.5 * k1), rate)
        k3 = derivative(max(0.0, previous + 0.5 * k2), rate)
        k4 = derivative(max(0.0, previous + k3), rate)
        projected[index] = max(0.0, previous + (k1 + 2 * k2 + 2 * k3 + k4) / 6.0)

    fluid_known = np.zeros_like(actual)
    fluid_known[0] = actual[0]
    fluid_train_estimated = np.zeros_like(actual)
    fluid_train_estimated[0] = actual[0]
    for index in range(1, len(actual)):
        fluid_known[index] = max(
            0.0,
            fluid_known[index - 1] + arrival_rate[index] - service_capacity_per_second,
        )
        fluid_train_estimated[index] = max(
            0.0,
            fluid_train_estimated[index - 1] + arrival_rate[index] - training_service_capacity,
        )

    persistence = np.full_like(actual, actual[0])
    observed = actual[1:]
    predictions = {
        "sdmodeler_unconstrained": unbounded[1:],
        "sdmodeler_nonnegative_projection": projected[1:],
        "reflected_queue_train_estimated_capacity": fluid_train_estimated[1:],
        "reflected_queue_configured_capacity": fluid_known[1:],
        "persistence": persistence[1:],
    }
    result = {}
    for name, prediction in predictions.items():
        residual = prediction - observed
        peak_index_actual = int(np.argmax(actual))
        peak_index_predicted = int(np.argmax(np.concatenate([[actual[0]], prediction])))
        actual_empty = np.flatnonzero((sample_times >= 60.0) & (actual <= 0.5))
        predicted_full = np.concatenate([[actual[0]], prediction])
        predicted_empty = np.flatnonzero((sample_times >= 60.0) & (predicted_full <= 0.5))
        result[name] = {
            "mae": float(np.mean(np.abs(residual))),
            "rmse": float(np.sqrt(np.mean(residual ** 2))),
            "normalized_rmse": normalized_rmse(observed.reshape(-1, 1), prediction.reshape(-1, 1)),
            "predicted_peak_queue": float(np.max(predicted_full)),
            "absolute_peak_error": float(abs(np.max(predicted_full) - np.max(actual))),
            "absolute_peak_time_error_seconds": float(abs(peak_index_predicted - peak_index_actual)),
            "observed_recovery_empty_seconds_after_t60": float(
                sample_times[actual_empty[0]] - 60.0 if len(actual_empty) else math.nan
            ),
            "predicted_recovery_empty_seconds_after_t60": float(
                sample_times[predicted_empty[0]] - 60.0 if len(predicted_empty) else math.nan
            ),
            "negative_prediction_count": int(np.count_nonzero(prediction < 0.0)),
        }
    return result


run_data = {run_id: load_run(run_id) for run_id in run_ids}
frames = {run_id: run_data[run_id][0] for run_id in run_ids}
fold_results = []
for held_out_id in run_ids:
    training_ids = [run_id for run_id in run_ids if run_id != held_out_id]
    training_service_times = [
        service_time
        for run_id in training_ids
        for service_time in run_data[run_id][1]
    ]
    mean_training_service_seconds = statistics.fmean(training_service_times)
    training_service_capacity = 1.0 / mean_training_service_seconds
    theta, dx = make_training_arrays([frames[run_id] for run_id in training_ids])
    optimizer = build_optimizer(config.optimizer, sparsity_threshold)
    optimizer.fit(theta, dx)
    coefficients = np.asarray(optimizer.coef_)
    model = SimpleNamespace(
        coefficients=coefficients,
        x_variables=["QueueDepth"],
        u_variables=["ArrivalRate"],
    )
    actual = frames[held_out_id]["QueueDepth"].to_numpy(dtype=float)
    arrival_rate = frames[held_out_id]["ArrivalRate"].to_numpy(dtype=float)
    score = evaluate(model, actual, arrival_rate, training_service_capacity)
    fold_results.append(
        {
            "held_out_run": held_out_id,
            "mean_training_service_seconds": mean_training_service_seconds,
            "training_service_capacity_per_second": training_service_capacity,
            "training_runs": training_ids,
            "coefficients_feature_order": ["QueueDepth", "1", "u:ArrivalRate"],
            "coefficients": coefficients.tolist(),
            "training_samples": int(theta.shape[0]),
            "fit_derivative_rmse": float(np.sqrt(np.mean((theta @ coefficients.T - dx) ** 2))),
            "observed_peak_queue": float(np.max(actual)),
            "observed_peak_time_seconds": int(np.argmax(actual)),
            "scores": score,
        }
    )

summary = {}
for candidate in fold_results[0]["scores"]:
    summary[candidate] = {
        metric: {
            "mean": float(np.mean([fold["scores"][candidate][metric] for fold in fold_results])),
            "min": float(np.min([fold["scores"][candidate][metric] for fold in fold_results])),
            "max": float(np.max([fold["scores"][candidate][metric] for fold in fold_results])),
        }
        for metric in (
            "mae",
            "rmse",
            "normalized_rmse",
            "absolute_peak_error",
            "absolute_peak_time_error_seconds",
        )
    }

result = {
    "experiment": "leave-one-run-out exploratory reflected-queue versus sparse-model test",
    "state": "waiting_depth reconstructed from accepted/completed event times under one-worker FIFO",
    "input": "scheduled arrival rate only; no held-out completion observations used as forecast inputs",
    "resampling_interval_seconds": 1,
    "schedule": [
        {"start_seconds": 0, "end_seconds": 30, "arrival_rate_per_second": 10},
        {"start_seconds": 30, "end_seconds": 60, "arrival_rate_per_second": 20},
        {"start_seconds": 60, "end_seconds": 90, "arrival_rate_per_second": 10},
    ],
    "configured_service_capacity_per_second": service_capacity_per_second,
    "reflected_model": {
        "equation": "Q[k] = max(0, Q[k-1] + arrival_rate[k] - training_only_service_capacity)",
        "service_capacity_estimator": "inverse mean service time inferred from training-run FIFO event times only",
    },
    "sdmodeler_method": {
        "feature_order": ["QueueDepth", "1", "u:ArrivalRate"],
        "differentiation": "Savitzky-Golay window 7, polynomial order 2",
        "optimizer": "STLSQ",
        "sparsity_threshold": sparsity_threshold,
        "training_selection": "fixed before evaluating held-out runs",
    },
    "folds": fold_results,
    "aggregate": summary,
}
output_path = project_root / "runs" / "queue-sdmodeler-hybrid-reflected-loro-exploratory-001.json"
output_path.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
print(f"saved_to={output_path}")
