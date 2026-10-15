import csv
import json
import statistics
from datetime import datetime
from pathlib import Path

root = Path(r"C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler\runs\queue-demo-pilot-001")
with (root / "app" / "telemetry.csv").open(newline="", encoding="utf-8") as stream:
    rows = list(csv.DictReader(stream))
for row in rows:
    row["time"] = datetime.fromisoformat(row["timestamp_utc"].replace("Z", "+00:00"))
    for key in ("accepted_total", "completed_total", "waiting_depth", "active_jobs", "unfinished_backlog"):
        row[key] = int(row[key])
intervals = [(b["time"] - a["time"]).total_seconds() for a, b in zip(rows, rows[1:])]
start = rows[0]["time"]
metadata = json.loads((root / "app" / "run_metadata.json").read_text(encoding="utf-8"))
summary = json.loads((root / "k6" / "k6-summary.json").read_text(encoding="utf-8"))
print("rows", len(rows), "interval_mean", statistics.mean(intervals), "interval_min", min(intervals), "interval_max", max(intervals))
print("duration_seconds", (rows[-1]["time"] - start).total_seconds())
print("max_backlog", max(row["unfinished_backlog"] for row in rows), "backlog_nonzero_samples", sum(row["unfinished_backlog"] > 0 for row in rows))
print("max_waiting", max(row["waiting_depth"] for row in rows), "max_active", max(row["active_jobs"] for row in rows))
print("conservation_violations", sum(row["unfinished_backlog"] != row["accepted_total"] - row["completed_total"] for row in rows))
for name, begin, finish in (("step_1", 0, 60), ("step_2", 60, 120), ("step_3", 120, 180), ("step_4", 180, 240)):
    phase = [row for row in rows if begin <= (row["time"] - start).total_seconds() < finish]
    accepted = phase[-1]["accepted_total"] - phase[0]["accepted_total"] if len(phase) > 1 else 0
    completed = phase[-1]["completed_total"] - phase[0]["completed_total"] if len(phase) > 1 else 0
    print(name, "samples", len(phase), "accepted_delta", accepted, "completed_delta", completed, "max_backlog", max((row["unfinished_backlog"] for row in phase), default=0))
print("metadata", metadata)
print("k6_outcomes", summary["iterations_completed"], summary["dropped_iterations"], summary["outcomes"], summary["latency_ms"])
