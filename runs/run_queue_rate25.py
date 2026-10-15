import csv
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

project_root = Path(r"C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler")
os.chdir(project_root)
sys.path.insert(0, str(project_root))

from experiments.queueing_demo.app import QueueingApp

run_id = "queue-demo-rate-25-001"
run_root = project_root / "runs" / run_id
app_dir = run_root / "app"
k6_dir = run_root / "k6"
k6_dir.mkdir(parents=True, exist_ok=False)
app = QueueingApp(
    host="127.0.0.1",
    port=0,
    worker_count=1,
    cpu_work_units=100000,
    drain_timeout_seconds=30,
    output_dir=app_dir,
    run_id=run_id,
    sampling_interval_seconds=1.0,
)
app.start()
result = None
try:
    address = app.base_url.removeprefix("http://").split(":")
    with socket.create_connection((address[0], int(address[1])), timeout=2):
        pass
    print(f"READY url={app.base_url} rate=25 duration=60s", flush=True)
    environment = os.environ.copy()
    environment.update(
        {
            "TARGET_URL": f"{app.base_url}/jobs",
            "RUN_ID": run_id,
            "OUTPUT_DIR": str(k6_dir),
            "RATE": "25",
            "DURATION": "60s",
        }
    )
    result = subprocess.run(
        [
            "k6",
            "run",
            "--quiet",
            "--out",
            f"json={k6_dir / 'metrics.json'}",
            r"C:\Users\jmfel\AppData\Local\Temp\queue_rate_sweep.js",
        ],
        env=environment,
        check=False,
    ).returncode
finally:
    clean = app.stop()
summary = json.loads((k6_dir / "k6-summary.json").read_text(encoding="utf-8"))
metadata = json.loads((app_dir / "run_metadata.json").read_text(encoding="utf-8"))
with (app_dir / "telemetry.csv").open(newline="", encoding="utf-8") as stream:
    max_backlog = max(int(row["unfinished_backlog"]) for row in csv.DictReader(stream))
outcomes = summary["outcomes"]
valid = (
    result == 0
    and clean
    and summary["dropped_iterations"] == 0
    and outcomes["non_accepted_responses"] == 0
    and outcomes["transport_errors"] == 0
    and metadata["worker_errors"] == 0
    and metadata["accepted_total"] == metadata["completed_total"]
)
print(
    json.dumps(
        {
            "run_id": run_id,
            "rate": 25,
            "k6_exit": result,
            "attempts": summary["request_attempts_completed"],
            "dropped": summary["dropped_iterations"],
            "accepted": outcomes["accepted"],
            "non_accepted": outcomes["non_accepted_responses"],
            "transport_errors": outcomes["transport_errors"],
            "http_failure_rate": outcomes["http_failure_rate"],
            "p95_ms": summary["latency_ms"]["p95"],
            "app_accepted": metadata["accepted_total"],
            "app_completed": metadata["completed_total"],
            "max_backlog": max_backlog,
            "clean_shutdown": clean,
            "valid": valid,
        },
        sort_keys=True,
    ),
    flush=True,
)
if not valid:
    raise SystemExit(1)
