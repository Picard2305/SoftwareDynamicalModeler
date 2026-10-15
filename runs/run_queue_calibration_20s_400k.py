import csv
import json
import os
import socket
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

project_root = Path(r"C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler")
os.chdir(project_root)
sys.path.insert(0, str(project_root))

from experiments.queueing_demo.app import QueueingApp

run_id = "queue-demo-calibration-20s-400k-001"
run_root = project_root / "runs" / run_id
app_dir = run_root / "app"
k6_dir = run_root / "k6"
k6_dir.mkdir(parents=True, exist_ok=False)
app = QueueingApp(
    host="127.0.0.1",
    port=0,
    worker_count=1,
    cpu_work_units=400000,
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
print(f"READY run_id={run_id} url={app.base_url} rate=20 duration=120s cpu_work_units=400000", flush=True)


def probe_listener():
    while not probe_stop.is_set():
        checked_at = datetime.now(timezone.utc).isoformat()
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


probe_thread = threading.Thread(target=probe_listener, name="queue-listener-probe-calibration", daemon=True)
probe_thread.start()
environment = os.environ.copy()
environment.update(
    {
        "TARGET_URL": f"{app.base_url}/jobs",
        "RUN_ID": run_id,
        "OUTPUT_DIR": str(k6_dir),
        "RATE": "20",
        "DURATION": "120s",
    }
)
result = None
try:
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
    probe_stop.set()
    probe_thread.join(timeout=2)
    server_thread_alive_at_stop = app._server_thread.is_alive()
    clean = app.stop()
    (run_root / "listener-probes.json").write_text(
        json.dumps(probe_results, indent=2) + "\n", encoding="utf-8"
    )

summary = json.loads((k6_dir / "k6-summary.json").read_text(encoding="utf-8"))
metadata = json.loads((app_dir / "run_metadata.json").read_text(encoding="utf-8"))
with (app_dir / "telemetry.csv").open(newline="", encoding="utf-8") as stream:
    telemetry_rows = list(csv.DictReader(stream))
max_backlog = max(int(row["unfinished_backlog"]) for row in telemetry_rows)
probe_failures = [item for item in probe_results if not item["connected"]]
outcomes = summary["outcomes"]
valid = (
    result == 0
    and clean
    and summary["dropped_iterations"] == 0
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
            "cpu_work_units": metadata["cpu_work_units"],
            "valid": valid,
            "k6_exit": result,
            "attempts": summary["request_attempts_completed"],
            "dropped": summary["dropped_iterations"],
            "accepted": outcomes["accepted"],
            "non_accepted": outcomes["non_accepted_responses"],
            "transport_errors": outcomes["transport_errors"],
            "http_failure_rate": outcomes["http_failure_rate"],
            "p95_http_ms": summary["latency_ms"]["p95"],
            "probe_count": len(probe_results),
            "probe_failures": len(probe_failures),
            "probe_failure_examples": probe_failures[:5],
            "app_server_thread_alive_at_stop": server_thread_alive_at_stop,
            "app_accepted": metadata["accepted_total"],
            "app_completed": metadata["completed_total"],
            "unfinished_backlog_at_shutdown": metadata["unfinished_backlog"],
            "max_sampled_backlog": max_backlog,
            "clean_shutdown": clean,
        },
        sort_keys=True,
    ),
    flush=True,
)
if not valid:
    raise SystemExit(1)
