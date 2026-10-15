import json
import os
import subprocess
import sys
from pathlib import Path

runner = Path(r"C:\Users\jmfel\AppData\Local\Temp\run_queue_service_time_profile.py")
k6_script = Path(r"C:\Users\jmfel\AppData\Local\Temp\queue_service_time_profile.js")
analyzer = Path(r"C:\Users\jmfel\AppData\Local\Temp\analyze_queue_new_arrival_study.py")
project_root = Path(r"C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler")

runs = [
    (profile, f"queue-demo-new-profile-{profile}-rep-{replicate:03d}")
    for replicate in range(1, 4)
    for profile in ("a", "b")
]
existing_runs = [run_id for _, run_id in runs if (project_root / "runs" / run_id).exists()]
if existing_runs:
    raise SystemExit(f"Refusing to reuse existing run directories: {existing_runs}")

for script in (runner, analyzer):
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
subprocess.run(["k6", "inspect", str(k6_script)], env=inspect_environment, check=True)

results = []
for profile, run_id in runs:
    print(f"START profile={profile} run_id={run_id}", flush=True)
    completed = subprocess.run(
        [sys.executable, str(runner), run_id, "--profile", profile],
        cwd=project_root,
        check=False,
    )
    results.append({"profile": profile, "run_id": run_id, "exit_code": completed.returncode})
    print(f"END profile={profile} run_id={run_id} exit_code={completed.returncode}", flush=True)

print({"runs": results, "valid_run_count": sum(item["exit_code"] == 0 for item in results)})
if any(item["exit_code"] != 0 for item in results):
    raise SystemExit(1)
