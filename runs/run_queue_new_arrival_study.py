import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = Path(__file__).with_name("run_queue_service_time_profile.py")
K6_SCRIPT = Path(__file__).with_name("queue_service_time_profile.js")
ANALYZER = Path(__file__).with_name("analyze_queue_new_arrival_study.py")

runs = [
    (profile, f"queue-demo-new-profile-{profile}-rep-{replicate:03d}")
    for replicate in range(1, 4)
    for profile in ("a", "b")
]
existing_runs = [run_id for _, run_id in runs if (PROJECT_ROOT / "runs" / run_id).exists()]
if existing_runs:
    raise SystemExit(f"Refusing to reuse existing run directories: {existing_runs}")

for script in (RUNNER, ANALYZER):
    compile(script.read_text(encoding="utf-8"), str(script), "exec")

inspect_environment = os.environ.copy()
inspect_environment["PHASES_JSON"] = json.dumps(
    [
        {
            "name": "preflight",
            "start_seconds": 0,
            "duration_seconds": 1,
            "rate_per_second": 1,
            "expected_requests": 1,
        }
    ]
)
subprocess.run(["k6", "inspect", str(K6_SCRIPT)], env=inspect_environment, check=True)

results = []
for profile, run_id in runs:
    print(f"START profile={profile} run_id={run_id}", flush=True)
    completed = subprocess.run(
        [sys.executable, str(RUNNER), run_id, "--profile", profile],
        cwd=PROJECT_ROOT,
        check=False,
    )
    results.append({"profile": profile, "run_id": run_id, "exit_code": completed.returncode})
    print(f"END profile={profile} run_id={run_id} exit_code={completed.returncode}", flush=True)

print({"runs": results, "valid_run_count": sum(item["exit_code"] == 0 for item in results)})
if any(item["exit_code"] != 0 for item in results):
    raise SystemExit(1)
