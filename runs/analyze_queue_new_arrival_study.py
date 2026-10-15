import csv
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

project_root = Path(r"C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler")
sys.path.insert(0, str(project_root))

from sdmodeler.config import DifferentiationMethod, FeatureLibraryTier, PipelineConfig
from sdmodeler.identification.differentiation import differentiate
from sdmodeler.identification.feature_library import build_feature_library, build_feature_row
from sdmodeler.identification.optimizer import build_optimizer
from sdmodeler.identification.plant_controller import build_plant_controller_data
from sdmodeler.validation import normalized_rmse, simulate_forward

training_run_ids = [
    "queue-demo-service-time-55ms-profile-002",
    "queue-demo-service-time-55ms-profile-003",
    "queue-demo-service-time-55ms-profile-004",
]
held_out_run_ids = [
    "queue-demo-new-profile-a-rep-002",
    "queue-demo-new-profile-a-rep-003",
    "queue-demo-new-profile-a-retry-001",
    "queue-demo-new-profile-b-rep-001",
    "queue-demo-new-profile-b-rep-002",
    "queue-demo-new-profile-b-retry-001",
    "queue-demo-new-profile-c-rep-001",
    "queue-demo-new-profile-c-retry-001",
    "queue-demo-new-profile-c-rep-003",
]
sparsity_threshold = 0.001

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
    run_root = project_root / "runs" / run_id
    app_dir = run_root / "app"
    with (run_root / "experiment_config.json").open(encoding="utf-8") as stream:
        experiment = json.load(stream)
    with (app_dir / "run_metadata.json").open(encoding="utf-8") as stream:
        metadata = json.load(stream)
    with (run_root / "k6" / "k6-summary.json").open(encoding="utf-8") as stream:
        k6_summary = json.load(stream)
    with (run_root / "listener-probes.json").open(encoding="utf-8") as stream:
        probes = json.load(stream)

    expected = sum(phase["expected_requests"] for phase in experiment["schedule"])
    valid = (
        metadata["worker_count"] == 1
        and metadata["clean_shutdown"]
        and metadata["accepted_total"] == expected
        and metadata["completed_total"] == expected
        and metadata["unfinished_backlog"] == 0
        and metadata["worker_errors"] == 0
        and k6_summary["request_attempts_started"] == expected
        and k6_summary["request_attempts_completed"] == expected
        and k6_summary["dropped_iterations"] == 0
        and k6_summary["outcomes"]["accepted"] == expected
        and k6_summary["outcomes"]["non_accepted_responses"] == 0
        and k6_summary["outcomes"]["transport_errors"] == 0
        and all(item["connected"] for item in probes)
    )
    if not valid:
        raise ValueError(f"Run failed prespecified validity checks: {run_id}")

    phases = experiment["schedule"]
    for index, phase in enumerate(phases):
        if "end_seconds" not in phase:
            phase["end_seconds"] = (
                phases[index + 1]["start_seconds"]
                if index + 1 < len(phases)
                else phase["start_seconds"] + 30
            )
    total_duration = max(phase["end_seconds"] for phase in phases)
    sample_times = np.arange(0.0, total_duration + 1.0, 1.0)

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
    if len(parsed) != expected:
        raise ValueError(f"Expected {expected} completed jobs in {run_id}, got {len(parsed)}")

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

    queue_depth = np.array(
        [sum(start <= t < end for start, end in waiting_intervals) for t in sample_times],
        dtype=float,
    )
    arrival_rate = np.array(
        [
            next(
                (
                    float(phase["rate_per_second"])
                    for phase in phases
                    if phase["start_seconds"] <= t < phase["end_seconds"]
                ),
                0.0,
            )
            for t in sample_times
        ],
        dtype=float,
    )
    origin = pd.Timestamp.fromtimestamp(load_start, tz="UTC")
    frame = pd.DataFrame(
        {"QueueDepth": queue_depth, "ArrivalRate": arrival_rate},
        index=pd.date_range(origin, periods=len(sample_times), freq="1s"),
    )
    return {
        "frame": frame,
        "service_times": service_times,
        "phases": phases,
        "profile": experiment.get("profile", "training"),
        "sample_times": sample_times,
        "service_time_seconds": experiment["service_time_seconds"],
    }


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


def evaluate(model, actual, arrival_rate, sample_times, service_capacity, phases):
    initial_state = np.array([0.0])
    unbounded = simulate_forward(
        model,
        initial_state,
        arrival_rate.reshape(-1, 1),
        1.0,
        config.feature_library,
    )[:, 0]
    projected = np.zeros_like(actual)

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

    reflected = np.zeros_like(actual)
    configured = np.zeros_like(actual)
    configured_capacity = 1.0 / 0.055
    for index in range(1, len(actual)):
        reflected[index] = max(
            0.0,
            reflected[index - 1] + arrival_rate[index] - service_capacity,
        )
        configured[index] = max(
            0.0,
            configured[index - 1] + arrival_rate[index] - configured_capacity,
        )

    predictions = {
        "sparse_unconstrained": unbounded,
        "sparse_nonnegative_projection": projected,
        "reflected_train_estimated_capacity": reflected,
        "reflected_configured_capacity_reference": configured,
        "empty_queue_baseline": np.zeros_like(actual),
    }
    recovery_start = next(
        (phase["start_seconds"] for phase in phases if phase["name"] == "recovery"),
        phases[-1]["start_seconds"],
    )
    results = {}
    for name, prediction in predictions.items():
        observed = actual[1:]
        forecast = prediction[1:]
        residual = forecast - observed
        observed_peak_index = int(np.argmax(actual))
        predicted_peak_index = int(np.argmax(prediction))
        observed_empty = np.flatnonzero((sample_times >= recovery_start) & (actual <= 0.5))
        predicted_empty = np.flatnonzero((sample_times >= recovery_start) & (prediction <= 0.5))
        results[name] = {
            "mae": float(np.mean(np.abs(residual))),
            "rmse": float(np.sqrt(np.mean(residual ** 2))),
            "normalized_rmse": normalized_rmse(observed.reshape(-1, 1), forecast.reshape(-1, 1)),
            "observed_peak_queue": float(np.max(actual)),
            "predicted_peak_queue": float(np.max(prediction)),
            "absolute_peak_error": float(abs(np.max(prediction) - np.max(actual))),
            "absolute_peak_time_error_seconds": float(
                abs(sample_times[predicted_peak_index] - sample_times[observed_peak_index])
            ),
            "observed_empty_seconds_after_recovery_start": (
                float(sample_times[observed_empty[0]] - recovery_start) if len(observed_empty) else None
            ),
            "predicted_empty_seconds_after_recovery_start": (
                float(sample_times[predicted_empty[0]] - recovery_start) if len(predicted_empty) else None
            ),
            "negative_prediction_count": int(np.count_nonzero(forecast < 0.0)),
        }
    return results


training_data = {run_id: load_run(run_id) for run_id in training_run_ids}
held_out_data = {run_id: load_run(run_id) for run_id in held_out_run_ids}
training_frames = [training_data[run_id]["frame"] for run_id in training_run_ids]
training_service_times = [
    service_time
    for run_id in training_run_ids
    for service_time in training_data[run_id]["service_times"]
]
mean_training_service_seconds = statistics.fmean(training_service_times)
training_service_capacity = 1.0 / mean_training_service_seconds
theta, dx = make_training_arrays(training_frames)
optimizer = build_optimizer(config.optimizer, sparsity_threshold)
optimizer.fit(theta, dx)
coefficients = np.asarray(optimizer.coef_)
model = SimpleNamespace(
    coefficients=coefficients,
    x_variables=["QueueDepth"],
    u_variables=["ArrivalRate"],
)

folds = []
for run_id, data in held_out_data.items():
    actual = data["frame"]["QueueDepth"].to_numpy(dtype=float)
    arrival_rate = data["frame"]["ArrivalRate"].to_numpy(dtype=float)
    scores = evaluate(
        model,
        actual,
        arrival_rate,
        data["sample_times"],
        training_service_capacity,
        data["phases"],
    )
    folds.append(
        {
            "run_id": run_id,
            "profile": data["profile"],
            "phases": data["phases"],
            "observed_peak_queue": float(np.max(actual)),
            "scores": scores,
        }
    )

metrics = (
    "mae",
    "rmse",
    "normalized_rmse",
    "absolute_peak_error",
    "absolute_peak_time_error_seconds",
)
aggregate = {}
for profile in sorted({fold["profile"] for fold in folds}):
    profile_folds = [fold for fold in folds if fold["profile"] == profile]
    aggregate[profile] = {}
    for model_name in profile_folds[0]["scores"]:
        aggregate[profile][model_name] = {
            metric: {
                "mean": float(np.mean([fold["scores"][model_name][metric] for fold in profile_folds])),
                "min": float(np.min([fold["scores"][model_name][metric] for fold in profile_folds])),
                "max": float(np.max([fold["scores"][model_name][metric] for fold in profile_folds])),
            }
            for metric in metrics
        }

result = {
    "experiment": "held-out new arrival profiles at fixed 55 ms service workload",
    "state": "waiting queue depth reconstructed from accepted/completed timestamps under one-worker FIFO",
    "forecast_inputs": "known arrival schedule and empty initial queue; no held-out completion or queue observations used as forecast inputs",
    "training_run_ids": training_run_ids,
    "held_out_run_ids": held_out_run_ids,
    "service_workload_seconds": 0.055,
    "training_mean_inferred_service_seconds": mean_training_service_seconds,
    "training_estimated_capacity_per_second": training_service_capacity,
    "configured_capacity_reference_per_second": 1.0 / 0.055,
    "training_samples": int(theta.shape[0]),
    "sparse_model": {
        "feature_order": ["QueueDepth", "1", "u:ArrivalRate"],
        "coefficients": coefficients.tolist(),
        "optimizer": "STLSQ",
        "sparsity_threshold": sparsity_threshold,
        "differentiation": "Savitzky-Golay window 7, polynomial order 2",
    },
    "reflected_model": {
        "equation": "Q[k] = max(0, Q[k-1] + (arrival_rate[k] - training_estimated_capacity) * 1 second)",
        "service_capacity_estimator": "inverse mean FIFO-inferred service duration from training runs only",
    },
    "folds": folds,
    "aggregate_by_profile": aggregate,
    "interpretation_limit": "three replicates per held-out profile; exploratory estimates, not a confirmatory significance test",
}
output_path = project_root / "runs" / "queue-new-arrival-profile-study-002.json"
output_path.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
print(f"saved_to={output_path}")
