import csv
import math
import statistics
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

for run_id in run_ids:
    path = project_root / "runs" / run_id / "app" / "jobs.csv"
    if not path.exists():
        continue
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    values = sorted(float(row["latency_seconds"]) for row in rows if row["latency_seconds"])
    if not values:
        print({"run_id": run_id, "count": 0})
        continue
    p95_index = max(0, math.ceil(0.95 * len(values)) - 1)
    print(
        {
            "run_id": run_id,
            "count": len(values),
            "mean_ms": statistics.fmean(values) * 1000,
            "median_ms": statistics.median(values) * 1000,
            "p95_ms": values[p95_index] * 1000,
            "max_ms": values[-1] * 1000,
        }
    )
