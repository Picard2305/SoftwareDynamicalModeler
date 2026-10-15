import os
import sys

project_root = r"C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler"
os.chdir(project_root)
sys.path.insert(0, project_root)

from experiments.queueing_demo.app import QueueingApp
from experiments.queueing_demo.k6_runner import run_k6

run_id = "queue-demo-pilot-001"
app = QueueingApp(
    host="127.0.0.1",
    port=8000,
    worker_count=1,
    cpu_work_units=100000,
    drain_timeout_seconds=30,
    output_dir=f"runs/{run_id}/app",
    run_id=run_id,
    sampling_interval_seconds=1.0,
)
app.start()
print(f"APP_URL={app.base_url}", flush=True)
result = None
try:
    result = run_k6(
        target_url=f"{app.base_url}/jobs",
        run_id=run_id,
        output_dir=f"runs/{run_id}/k6",
        job_type="cpu",
        smoke=False,
        k6_version="k6.exe v2.2.0",
    )
finally:
    clean = app.stop()
    print(f"K6_EXIT={result} CLEAN_SHUTDOWN={clean}", flush=True)
if result != 0 or not clean:
    raise SystemExit(1)
