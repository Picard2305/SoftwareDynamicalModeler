import csv
import csv
import json
import math
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

project_root = Path(r"C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler")
os.chdir(project_root)
sys.path.insert(0, str(project_root))

from experiments.queueing_demo.app import QueueingApp

import argparse

parser = argparse.ArgumentParser()
parser.add_argument("run_id")
parser.add_argument("--profile", choices=("original", "a", "b", "c"), default="original")
args = parser.parse_args()
run_id = args.run_id
service_time_seconds = 0.055
profiles = {
    "original": [
        {"name": "baseline", "start_seconds": 0, "end_seconds": 30, "duration_seconds": 29.99, "rate_per_second": 10, "expected_requests": 300},
        {"name": "overload", "start_seconds": 30, "end_seconds": 60, "duration_seconds": 29.99, "rate_per_second": 20, "expected_requests": 600},
        {"name": "recovery", "start_seconds": 60, "end_seconds": 90, "duration_seconds": 29.99, "rate_per_second": 10, "expected_requests": 300},
    ],
    "a": [
        {"name": "baseline", "start_seconds": 0, "end_seconds": 30, "duration_seconds": 29.99, "rate_per_second": 8, "expected_requests": 240},
        {"name": "overload", "start_seconds": 30, "end_seconds": 60, "duration_seconds": 29.99, "rate_per_second": 22, "expected_requests": 660},
        {"name": "recovery", "start_seconds": 60, "end_seconds": 90, "duration_seconds": 29.99, "rate_per_second": 8, "expected_requests": 240},
    ],
    "b": [
        {"name": "baseline", "start_seconds": 0, "end_seconds": 20, "duration_seconds": 19.99, "rate_per_second": 10, "expected_requests": 200},
        {"name": "ramp_low", "start_seconds": 20, "end_seconds": 40, "duration_seconds": 19.99, "rate_per_second": 21, "expected_requests": 420},
        {"name": "ramp_high", "start_seconds": 40, "end_seconds": 60, "duration_seconds": 19.99, "rate_per_second": 23, "expected_requests": 460},
        {"name": "recovery", "start_seconds": 60, "end_seconds": 90, "duration_seconds": 29.99, "rate_per_second": 10, "expected_requests": 300},
    ],
    "c": [
        {"name": "baseline", "start_seconds": 0, "end_seconds": 15, "duration_seconds": 14.99, "rate_per_second": 12, "expected_requests": 180},
        {"name": "overload_low", "start_seconds": 15, "end_seconds": 30, "duration_seconds": 14.99, "rate_per_second": 20, "expected_requests": 300},
        {"name": "overload_high", "start_seconds": 30, "end_seconds": 45, "duration_seconds": 14.99, "rate_per_second": 23, "expected_requests": 345},
        {"name": "partial_recovery", "start_seconds": 45, "end_seconds": 60, "duration_seconds": 14.99, "rate_per_second": 16, "expected_requests": 240},
        {"name": "recovery", "start_seconds": 60, "end_seconds": 90, "duration_seconds": 29.99, "rate_per_second": 8, "expected_requests": 240},
    ],
}
schedule = profiles[args.profile]
run_root = project_root / "runs" / run_id
app_dir = run_root / "app"
k6_dir = run_root / "k6"
k6_dir.mkdir(parents=True, exist_ok=False)
(run_root / "experiment_config.json").write_text(
    json.dumps(
        {
            "run_id": run_id,
            "profile": args.profile,
            "processor": "time.sleep",
            "service_time_seconds": service_time_seconds,
            "worker_count": 1,
            "cpu_work_units": 0,
            "sampling_interval_seconds": 1.0,
            "schedule": schedule,
            "expected_requests": sum(phase["expected_requests"] for phase in schedule),
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)


def controlled_processor(job):
    time.sleep(service_time_seconds)


app = QueueingApp(
    host="127.0.0.1",
    port=0,
    worker_count=1,
    processor=controlled_processor,
    cpu_work_units=0,
    drain_timeout_seconds=30,
    output_dir=app_dir,
    run_id=run_id,
    sampling_interval_seconds=1.0,
)
app.start()
address = urlsplit(app.base_url)
host = address.hostname
port = address.port
probe_stop = threading.Event()
probe_results = []

with socket.create_connection((host, port), timeout=2):
    pass
print(
    f"READY run_id={run_id} url={app.base_url} profile={args.profile} service_time_seconds={service_time_seconds} schedule={[(phase['rate_per_second'], phase['end_seconds'] - phase['start_seconds']) for phase in schedule]}",
    flush=True,
)


def probe_listener():
    while not probe_stop.is_set():
        checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        server_thread_alive = app._server_thread.is_alive()
        try:
            with socket.create_connection((host, port), timeout=0.2):
                probe_results.append(
                    {"timestamp_utc": checked_at, "connected": True, "server_thread_alive": server_thread_alive}
                )
        except OSError as error:
            probe_results.append(
                {
                    "timestamp_utc": checked_at,
                    "connected": False,
                    "server_thread_alive": server_thread_alive,
                    "error": str(error),
                }
            )
        probe_stop.wait(0.2)


probe_thread = threading.Thread(target=probe_listener, name="queue-service-profile-probe", daemon=True)
probe_thread.start()
environment = os.environ.copy()
environment.update(
    {
        "TARGET_URL": f"{app.base_url}/jobs",
        "RUN_ID": run_id,
        "OUTPUT_DIR": str(k6_dir),
        "PHASES_JSON": json.dumps(schedule),
    }
)
try:
    k6_result = subprocess.run(
        [
            "k6",
            "run",
            "--quiet",
            "--out",
            f"json={k6_dir / 'metrics.json'}",
            r"C:\Users\jmfel\AppData\Local\Temp\queue_service_time_profile.js",
        ],
        env=environment,
        check=False,
    ).returncode
finally:
    probe_stop.set()
    probe_thread.join(timeout=2)
    server_thread_alive_at_stop = app._server_thread.is_alive()
    clean_shutdown = app.stop()
    (run_root / "listener-probes.json").write_text(
        json.dumps(probe_results, indent=2) + "\n", encoding="utf-8"
    )

summary_path = k6_dir / "k6-summary.json"
if not summary_path.exists():
    raise RuntimeError("k6 did not produce k6-summary.json")
summary = json.loads(summary_path.read_text(encoding="utf-8"))
metadata = json.loads((app_dir / "run_metadata.json").read_text(encoding="utf-8"))
with (app_dir / "telemetry.csv").open(newline="", encoding="utf-8") as stream:
    telemetry_rows = list(csv.DictReader(stream))
max_sampled_backlog = max(int(row["unfinished_backlog"]) for row in telemetry_rows)
probe_failures = [item for item in probe_results if not item["connected"]]

with (app_dir / "jobs.csv").open(newline="", encoding="utf-8") as stream:
    job_rows = list(csv.DictReader(stream))
completed_jobs = [row for row in job_rows if row["completed_at_utc"] and row["accepted_at_utc"]]
events = []
waiting_seconds = []
previous_completion = float("-inf")
for row in sorted(completed_jobs, key=lambda item: item["accepted_at_utc"]):
    accepted_at = datetime.fromisoformat(row["accepted_at_utc"].replace("Z", "+00:00")).timestamp()
    completed_at = datetime.fromisoformat(row["completed_at_utc"].replace("Z", "+00:00")).timestamp()
    started_at = max(accepted_at, previous_completion)
    waiting_seconds.append(max(0.0, started_at - accepted_at))
    events.append((accepted_at, 1))
    events.append((completed_at, -1))
    previous_completion = completed_at

events.sort(key=lambda event: (event[0], event[1]))
in_system = 0
max_event_in_system = 0
area_in_system = 0.0
previous_event_time = events[0][0] if events else 0.0
for event_time, change in events:
    area_in_system += in_system * (event_time - previous_event_time)
    in_system += change
    max_event_in_system = max(max_event_in_system, in_system)
    previous_event_time = event_time
elapsed = events[-1][0] - events[0][0] if len(events) > 1 else 0.0
phase_job_counts = {
    phase["name"]: sum(
        row["job_id"].startswith(f"{run_id}-{phase['name']}-") for row in completed_jobs
    )
    for phase in schedule
}
queue_summary = {
    "completed_jobs": len(completed_jobs),
    "phase_job_counts": phase_job_counts,
    "max_event_reconstructed_in_system": max_event_in_system,
    "jobs_with_inferred_wait": sum(value > 0.001 for value in waiting_seconds),
    "mean_inferred_wait_ms": statistics.fmean(waiting_seconds) * 1000 if waiting_seconds else 0.0,
    "p95_inferred_wait_ms": sorted(waiting_seconds)[max(0, math.ceil(0.95 * len(waiting_seconds)) - 1)] * 1000
    if waiting_seconds
    else 0.0,
    "time_weighted_mean_in_system": area_in_system / elapsed if elapsed else 0.0,
    "max_sampled_backlog": max_sampled_backlog,
}
(run_root / "queue-summary.json").write_text(
    json.dumps(queue_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
outcomes = summary["outcomes"]
valid = (
    k6_result == 0
    and clean_shutdown
    and summary["dropped_iterations"] == 0
    and summary["request_attempts_started"] == summary["expected_requests"]
    and summary["request_attempts_completed"] == summary["expected_requests"]
    and outcomes["accepted"] == summary["expected_requests"]
    and phase_job_counts == {
        phase["name"]: phase["expected_requests"] for phase in schedule
    }
    and outcomes["non_accepted_responses"] == 0
    and outcomes["transport_errors"] == 0
    and not probe_failures
    and metadata["worker_errors"] == 0
    and metadata["accepted_total"] == outcomes["accepted"]
    and metadata["accepted_total"] == metadata["completed_total"]
    and metadata["unfinished_backlog"] == 0
)
print(
    json.dumps(
        {
            "run_id": run_id,
            "valid": valid,
            "k6_exit": k6_result,
            "expected_requests": summary["expected_requests"],
            "attempts": summary["request_attempts_completed"],
            "dropped_iterations": summary["dropped_iterations"],
            "accepted": outcomes["accepted"],
            "transport_errors": outcomes["transport_errors"],
            "non_accepted": outcomes["non_accepted_responses"],
            "p95_http_ms": summary["latency_ms"]["p95"],
            "probe_count": len(probe_results),
            "probe_failures": len(probe_failures),
            "app_accepted": metadata["accepted_total"],
            "app_completed": metadata["completed_total"],
            "worker_errors": metadata["worker_errors"],
            "unfinished_backlog_at_shutdown": metadata["unfinished_backlog"],
            "clean_shutdown": clean_shutdown,
            "app_server_thread_alive_at_stop": server_thread_alive_at_stop,
            "queue_summary": queue_summary,
        },
        sort_keys=True,
    ),
    flush=True,
)
if not valid:
    raise SystemExit(1)
