import csv
import json
import os
import threading
from pathlib import Path


TELEMETRY_FIELDS = (
    "timestamp_utc",
    "accepted_total",
    "completed_total",
    "waiting_depth",
    "active_jobs",
    "unfinished_backlog",
    "completed_since_previous_sample",
    "completed_job_latency_count",
    "completed_latency_mean_seconds",
    "completed_latency_median_seconds",
    "completed_latency_p95_seconds",
    "completed_latency_max_seconds",
)
JOB_FIELDS = (
    "run_id",
    "job_id",
    "submitted_at",
    "accepted_at_utc",
    "completed_at_utc",
    "latency_seconds",
    "result",
)


class RunArtifacts:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self._lock = threading.Lock()
        self._closed = False
        self.row_count = 0
        self._telemetry_stream = (self.output_dir / "telemetry.csv").open(
            "x", newline="", encoding="utf-8"
        )
        self._telemetry_writer = csv.DictWriter(
            self._telemetry_stream,
            fieldnames=TELEMETRY_FIELDS,
            extrasaction="ignore",
        )
        self._telemetry_writer.writeheader()
        self._telemetry_stream.flush()

    def record_sample(self, sample):
        with self._lock:
            if self._closed:
                return
            self._telemetry_writer.writerow(sample)
            self._telemetry_stream.flush()
            self.row_count += 1

    def finalize(self, jobs, metadata):
        with self._lock:
            if self._closed:
                return
            self._telemetry_stream.flush()
            self._telemetry_stream.close()
            self._closed = True
            jobs_path = self.output_dir / "jobs.csv"
            jobs_temp = self.output_dir / "jobs.csv.tmp"
            with jobs_temp.open("x", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=JOB_FIELDS)
                writer.writeheader()
                for job in jobs:
                    writer.writerow(job)
            os.replace(jobs_temp, jobs_path)
            metadata["row_count"] = self.row_count
            metadata_path = self.output_dir / "run_metadata.json"
            metadata_temp = self.output_dir / "run_metadata.json.tmp"
            with metadata_temp.open("x", encoding="utf-8") as stream:
                json.dump(metadata, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(metadata_temp, metadata_path)
