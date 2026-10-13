import csv
import json
import threading
import urllib.request
from datetime import datetime, timezone

import pytest

from experiments.queueing_demo.app import QueueingApp


JOB = {
    "run_id": "run-telemetry",
    "job_id": "job-telemetry",
    "submitted_at": "2026-10-01T12:00:00Z",
    "job_type": "cpu",
}


def _read_csv(path):
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def _parse_utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    return parsed


def test_run_artifacts_include_sampled_telemetry_and_job_correlation(tmp_path):
    processed = threading.Event()
    output_dir = tmp_path / "run-output"
    app = QueueingApp(
        host="127.0.0.1",
        port=0,
        worker_count=1,
        processor=lambda job: processed.set(),
        output_dir=output_dir,
        run_id=JOB["run_id"],
        sampling_interval_seconds=0.01,
    )
    app.start()
    try:
        request = urllib.request.Request(
            f"{app.base_url}/jobs",
            data=json.dumps(JOB).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 202
        assert processed.wait(2)
        assert app.stop()

        telemetry_fields, telemetry_rows = _read_csv(output_dir / "telemetry.csv")
        jobs_fields, jobs_rows = _read_csv(output_dir / "jobs.csv")
        metadata = json.loads((output_dir / "run_metadata.json").read_text(encoding="utf-8"))

        assert {
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
        }.issubset(telemetry_fields)
        assert telemetry_rows
        timestamps = [_parse_utc(row["timestamp_utc"]) for row in telemetry_rows]
        assert timestamps == sorted(timestamps)
        accepted_counts = [int(row["accepted_total"]) for row in telemetry_rows]
        completed_counts = [int(row["completed_total"]) for row in telemetry_rows]
        assert accepted_counts == sorted(accepted_counts)
        assert completed_counts == sorted(completed_counts)
        assert int(telemetry_rows[-1]["unfinished_backlog"]) == 0
        assert int(telemetry_rows[-1]["accepted_total"]) == int(
            telemetry_rows[-1]["completed_total"]
        )
        assert any(int(row["completed_job_latency_count"]) >= 1 for row in telemetry_rows)

        assert {
            "run_id",
            "job_id",
            "submitted_at",
            "accepted_at_utc",
            "completed_at_utc",
            "latency_seconds",
            "result",
        }.issubset(jobs_fields)
        assert len(jobs_rows) == 1
        assert jobs_rows[0]["run_id"] == JOB["run_id"]
        assert jobs_rows[0]["job_id"] == JOB["job_id"]
        assert jobs_rows[0]["submitted_at"] == JOB["submitted_at"]
        assert jobs_rows[0]["result"] == "success"
        _parse_utc(jobs_rows[0]["accepted_at_utc"])
        _parse_utc(jobs_rows[0]["completed_at_utc"])

        assert metadata["run_id"] == JOB["run_id"]
        assert metadata["row_count"] == len(telemetry_rows)
        assert metadata["accepted_total"] == metadata["completed_total"]
        assert metadata["unfinished_backlog"] == 0
        assert metadata["clean_shutdown"] is True
        _parse_utc(metadata["start_time_utc"])
        _parse_utc(metadata["end_time_utc"])
    finally:
        app.stop()


def test_drain_timeout_still_finalizes_valid_partial_artifacts(tmp_path):
    started = threading.Event()
    release = threading.Event()
    processed = threading.Event()
    output_dir = tmp_path / "partial-run"

    def processor(job):
        started.set()
        release.wait(2)
        processed.set()

    app = QueueingApp(
        host="127.0.0.1",
        port=0,
        processor=processor,
        output_dir=output_dir,
        run_id=JOB["run_id"],
        drain_timeout_seconds=0,
    )
    app.start()
    try:
        request = urllib.request.Request(
            f"{app.base_url}/jobs",
            data=json.dumps(JOB).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 202
        assert started.wait(2)

        assert not app.stop()
        assert not app.stop()

        telemetry_fields, telemetry_rows = _read_csv(output_dir / "telemetry.csv")
        jobs_fields, jobs_rows = _read_csv(output_dir / "jobs.csv")
        metadata = json.loads((output_dir / "run_metadata.json").read_text(encoding="utf-8"))

        assert "unfinished_backlog" in telemetry_fields
        assert telemetry_rows
        assert len(jobs_rows) == 1
        assert jobs_rows[0]["result"] == "unfinished"
        assert metadata["clean_shutdown"] is False
        assert metadata["unfinished_backlog"] > 0
    finally:
        release.set()
        app.stop()
        assert processed.wait(2)


def test_existing_run_directory_is_not_overwritten(tmp_path):
    output_dir = tmp_path / "existing-run"
    output_dir.mkdir()
    marker = output_dir / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    app = QueueingApp(
        host="127.0.0.1",
        port=0,
        output_dir=output_dir,
        run_id=JOB["run_id"],
    )

    with pytest.raises(FileExistsError):
        app.start()

    assert marker.read_text(encoding="utf-8") == "preserve"
