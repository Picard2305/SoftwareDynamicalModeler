import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_k6_launcher_claims_new_output_before_invoking_k6(tmp_path, monkeypatch):
    from experiments.queueing_demo import k6_runner

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(k6_runner.subprocess, "run", fake_run)
    output_dir = tmp_path / "new-run"

    result = k6_runner.run_k6(
        target_url="http://127.0.0.1:8000/jobs",
        run_id="new-run",
        output_dir=output_dir,
        k6_executable="fake-k6",
    )

    assert result == 0
    assert output_dir.is_dir()
    assert len(calls) == 1
    assert str(output_dir / "metrics.json") in calls[0][0][3]
    assert calls[0][1]["env"]["OUTPUT_DIR"] == str(output_dir)
    with pytest.raises(FileExistsError, match="already exists"):
        k6_runner.run_k6(
            target_url="http://127.0.0.1:8000/jobs",
            run_id="new-run",
            output_dir=output_dir,
            k6_executable="fake-k6",
        )
    assert len(calls) == 1


def test_k6_launcher_refuses_existing_output_without_changing_it(tmp_path):
    output_dir = tmp_path / "existing-run"
    output_dir.mkdir()
    sentinel = output_dir / "metrics.json"
    sentinel.write_text("previous run data", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "experiments.queueing_demo.k6_runner",
            "--output-dir",
            str(output_dir),
            "--target-url",
            "http://127.0.0.1:8000/jobs",
            "--run-id",
            "existing-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "previous run data"


def test_k6_job_ids_remain_unique_across_sequential_scenarios(tmp_path):
    k6 = shutil.which("k6")
    if k6 is None:
        pytest.skip("k6 is required for this integration test")

    requests = []

    class JobHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload_size = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(payload_size))
            requests.append((time.monotonic(), payload))
            self.send_response(202)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), JobHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        source_path = (
            Path(__file__).parents[1]
            / "experiments"
            / "queueing_demo"
            / "load.js"
        )
        source = source_path.read_text(encoding="utf-8")
        schedule_selector = "const schedule = smokeMode ? smokeSchedule : stepSchedule;"
        short_schedule = '''const schedule = smokeMode ? smokeSchedule : [
  { name: "first", startTime: "0s", duration: "2s", rate: 2, preAllocatedVUs: 2, maxVUs: 4 },
  { name: "second", startTime: "2s", duration: "2s", rate: 2, preAllocatedVUs: 2, maxVUs: 4 },
];'''
        assert schedule_selector in source
        test_script = tmp_path / "load.js"
        test_script.write_text(source.replace(schedule_selector, short_schedule), encoding="utf-8")
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        metrics_path = output_dir / "metrics.json"
        environment = os.environ.copy()
        environment.update(
            {
                "TARGET_URL": f"http://127.0.0.1:{server.server_port}/jobs",
                "RUN_ID": "cross-step-test",
                "JOB_TYPE": "cpu",
                "OUTPUT_DIR": str(output_dir),
                "SMOKE": "0",
            }
        )
        started_at = time.monotonic()
        result = subprocess.run(
            [k6, "run", "--out", f"json={metrics_path}", str(test_script)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        observed = [record for record in requests if record[0] - started_at >= 2]
        assert observed
        job_ids = [payload["job_id"] for _, payload in requests]
        assert len(job_ids) > 1
        assert len(set(job_ids)) == len(job_ids)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_k6_workload_uses_open_model_and_one_request_per_iteration():
    script_path = (
        Path(__file__).parents[1]
        / "experiments"
        / "queueing_demo"
        / "load.js"
    )
    script = script_path.read_text(encoding="utf-8")

    assert 'import http from "k6/http";' in script
    assert "constant-arrival-rate" in script
    assert "preAllocatedVUs" in script
    assert "maxVUs" in script
    assert script.count("http.post(") == 1
    assert "exec.scenario.iterationInTest" in script
    assert all(name in script for name in ("step_1", "step_2", "step_3", "step_4"))
    assert "startTime" in script
    assert "duration" in script
    assert "sleep(" not in script
    assert "Math.random" not in script
    assert "ramping-vus" not in script


def test_k6_summary_contains_numeric_counts_and_consistent_latency_fields(tmp_path):
    k6 = shutil.which("k6")
    if k6 is None:
        pytest.skip("k6 is required for this integration test")

    received = []

    class JobHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(202)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), JobHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        source_path = (
            Path(__file__).parents[1]
            / "experiments"
            / "queueing_demo"
            / "load.js"
        )
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        environment = os.environ.copy()
        environment.update(
            {
                "TARGET_URL": f"http://127.0.0.1:{server.server_port}/jobs",
                "RUN_ID": "summary-test",
                "JOB_TYPE": "cpu",
                "OUTPUT_DIR": str(output_dir),
                "SMOKE": "1",
            }
        )
        result = subprocess.run(
            [k6, "run", str(source_path)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        summary_path = output_dir / "k6-summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        count_values = [
            summary["iterations_completed"],
            summary["requests_completed"],
            summary["request_attempts_started"],
            summary["request_attempts_completed"],
            summary["dropped_iterations"],
            summary["outcomes"]["accepted"],
            summary["outcomes"]["non_accepted_responses"],
            summary["outcomes"]["transport_errors"],
            summary["latency_ms"]["count"],
        ]
        assert received
        assert all(isinstance(value, (int, float)) and value >= 0 for value in count_values)
        assert summary["requests_completed"] == len(received)
        assert summary["outcomes"]["accepted"] == len(received)
        assert summary["request_attempts_started"] == summary["request_attempts_completed"]
        assert summary["latency_ms"]["count"] == summary["requests_completed"]
        assert isinstance(summary["outcomes"]["http_failure_rate"], (int, float))
        assert all(
            isinstance(summary["latency_ms"][field], (int, float))
            and summary["latency_ms"][field] >= 0
            for field in ("mean", "median", "p95", "max")
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_k6_workload_keeps_target_and_run_settings_external():
    script_path = (
        Path(__file__).parents[1]
        / "experiments"
        / "queueing_demo"
        / "load.js"
    )
    script = script_path.read_text(encoding="utf-8")

    assert "__ENV.TARGET_URL" in script
    assert "__ENV.RUN_ID" in script
    assert "__ENV.JOB_TYPE" in script
    assert "__ENV.OUTPUT_DIR" in script
    assert "__ENV.SMOKE" in script
    assert "dropped_iterations" in script
    assert 'new Counter("request_attempts_started")' in script
    assert 'new Counter("request_attempts_completed")' in script
    assert "request_attempts_started:" in script
    assert "request_attempts_completed:" in script
    assert "handleSummary" in script
    assert "discardResponseBodies: true" in script
    assert "response.body" not in script
    assert "console.log" not in script
    assert "http://127.0.0.1" not in script
