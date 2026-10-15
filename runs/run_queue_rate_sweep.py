import json
import os
import subprocess
import sys
from pathlib import Path

project_root = Path(r"C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler")
os.chdir(project_root)
sys.path.insert(0, str(project_root))

from experiments.queueing_demo.app import QueueingApp

run_root = project_root / "runs" / "queue-demo-rate-sweep-001"
script_path = Path(r"C:\Users\jmfel\AppData\Local\Temp\queue_rate_sweep.js")
rates = (20, 25, 30, 35, 40)

for rate in rates:
    run_id = f"queue-demo-rate-{rate:02d}"
    app_dir = run_root / run_id / "app"
    k6_dir = run_root / run_id / "k6"
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
    print(f"START rate={rate} url={app.base_url}", flush=True)
    environment = os.environ.copy()
    environment.update(
        {
            "TARGET_URL": f"{app.base_url}/jobs",
            "RUN_ID": run_id,
            "OUTPUT_DIR": str(k6_dir),
            "RATE": str(rate),
            "DURATION": "60s",
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
                str(script_path),
            ],
            env=environment,
            check=False,
        ).returncode
    finally:
        clean = app.stop()
    summary_path = k6_dir / "k6-summary.json"
    if not summary_path.exists():
        print(f"STOP rate={rate} k6_exit={result} clean_shutdown={clean} summary_missing=true", flush=True)
        raise SystemExit(2)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metadata = json.loads((app_dir / "run_metadata.json").read_text(encoding="utf-8"))
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
                "rate": rate,
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
                "max_backlog": max(
                    int(row["unfinished_backlog"])
                    for row in __import__("csv").DictReader(
                        (app_dir / "telemetry.csv").open(newline="", encoding="utf-8")
                    )
                ),
                "clean_shutdown": clean,
                "valid": valid,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if not valid:
        print(f"STOPPED at rate={rate}; higher-rate stages were not run.", flush=True)
        break
