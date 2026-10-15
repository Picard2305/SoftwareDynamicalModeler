import csv
import math
import statistics
from datetime import datetime
from pathlib import Path

project_root = Path(r"C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler")
run_ids = [
    "queue-demo-rate-20-replicate-001",
    "queue-demo-rate-20-replicate-002",
    "queue-demo-rate-20-replicate-003",
    "queue-demo-calibration-20s-250k-001",
    "queue-demo-calibration-20s-275k-001",
    "queue-demo-calibration-20s-280k-001",
    "queue-demo-calibration-20s-285k-001",
    "queue-demo-calibration-20s-300k-001",
    "queue-demo-calibration-20s-400k-001",
]


def parse_timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def percentile(values, p):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


for run_id in run_ids:
    path = project_root / "runs" / run_id / "app" / "jobs.csv"
    if not path.exists():
        continue
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    jobs = [
        {
            "accepted": parse_timestamp(row["accepted_at_utc"]),
            "completed": parse_timestamp(row["completed_at_utc"]),
            "latency": float(row["latency_seconds"]),
        }
        for row in rows
        if row["completed_at_utc"] and row["latency_seconds"]
    ]
    jobs.sort(key=lambda job: job["accepted"])
    previous_completion = float("-inf")
    events = []
    waiting_values = []
    service_values = []
    for job in jobs:
        started = max(job["accepted"], previous_completion)
        wait = started - job["accepted"]
        service = job["completed"] - started
        waiting_values.append(max(0.0, wait))
        service_values.append(max(0.0, service))
        events.append((job["accepted"], 1))
        events.append((job["completed"], -1))
        previous_completion = job["completed"]

    events.sort(key=lambda event: (event[0], event[1]))
    in_system = 0
    max_in_system = 0
    area_in_system = 0.0
    previous_time = events[0][0]
    for timestamp, change in events:
        area_in_system += in_system * (timestamp - previous_time)
        in_system += change
        max_in_system = max(max_in_system, in_system)
        previous_time = timestamp

    elapsed = events[-1][0] - events[0][0]
    waiting_count = sum(value > 1e-6 for value in waiting_values)
    print(
        {
            "run_id": run_id,
            "completed_jobs": len(jobs),
            "event_reconstructed_max_in_system": max_in_system,
            "time_weighted_mean_in_system": area_in_system / elapsed if elapsed else 0.0,
            "jobs_with_inferred_wait": waiting_count,
            "mean_wait_ms": statistics.fmean(waiting_values) * 1000,
            "p95_wait_ms": percentile(waiting_values, 0.95) * 1000,
            "mean_service_ms": statistics.fmean(service_values) * 1000,
            "p95_service_ms": percentile(service_values, 0.95) * 1000,
            "mean_accept_to_complete_ms": statistics.fmean(job["latency"] for job in jobs) * 1000,
            "p95_accept_to_complete_ms": percentile([job["latency"] for job in jobs], 0.95) * 1000,
        }
    )
