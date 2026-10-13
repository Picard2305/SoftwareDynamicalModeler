import json
import json
import threading
import urllib.error
import urllib.request

import pytest

from experiments.queueing_demo.app import QueueingApp


VALID_JOB = {
    "run_id": "run-a",
    "job_id": "job-a",
    "submitted_at": "2026-10-01T12:00:00Z",
    "job_type": "cpu",
}


def _start_app(tmp_path, processor, worker_count=1):
    app = QueueingApp(
        host="127.0.0.1",
        port=0,
        worker_count=worker_count,
        processor=processor,
        output_dir=tmp_path / "run",
        run_id=VALID_JOB["run_id"],
    )
    app.start()
    return app


def _request(app, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{app.base_url}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        response = urllib.request.urlopen(request, timeout=2)
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    with response:
        return response.status, response.read()


def test_post_jobs_acknowledges_enqueue_and_worker_receives_job(tmp_path):
    received = []
    processed = threading.Event()

    def processor(job):
        received.append(job)
        processed.set()

    app = _start_app(tmp_path, processor)
    try:
        status, body = _request(app, "/jobs", VALID_JOB)

        assert status == 202
        assert json.loads(body)["job_id"] == VALID_JOB["job_id"]
        assert processed.wait(2)
        assert received == [VALID_JOB]
    finally:
        app.stop()


def test_default_cpu_processor_finishes_accepted_job_on_shutdown(tmp_path):
    app = QueueingApp(
        host="127.0.0.1",
        port=0,
        cpu_work_units=100,
        output_dir=tmp_path / "run",
        run_id=VALID_JOB["run_id"],
    )
    app.start()
    assert _request(app, "/jobs", VALID_JOB)[0] == 202

    assert app.stop()
    assert app.snapshot()["completed_total"] == app.snapshot()["accepted_total"]


@pytest.mark.parametrize("worker_count", [0, -1])
def test_worker_count_must_be_positive(worker_count):
    with pytest.raises(ValueError):
        QueueingApp(worker_count=worker_count)


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        {"run_id": "run-a", "job_id": "job-a", "submitted_at": "2026-10-01T12:00:00Z"},
        {**VALID_JOB, "submitted_at": "not-a-timestamp"},
        {**VALID_JOB, "job_type": "unsupported"},
    ],
)
def test_invalid_jobs_are_rejected_without_processing(body, tmp_path):
    app = _start_app(tmp_path, lambda job: None)
    try:
        if isinstance(body, bytes):
            request = urllib.request.Request(
                f"{app.base_url}/jobs",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            try:
                response = urllib.request.urlopen(request, timeout=2)
            except urllib.error.HTTPError as error:
                status = error.code
            else:
                with response:
                    status = response.status
        else:
            status, _ = _request(app, "/jobs", body)

        assert 400 <= status < 500
        assert app.snapshot()["accepted_total"] == 0
    finally:
        app.stop()


def test_duplicate_job_ids_are_rejected_within_a_run(tmp_path):
    processed = threading.Event()
    app = _start_app(tmp_path, lambda job: processed.set())
    try:
        assert _request(app, "/jobs", VALID_JOB)[0] == 202
        assert processed.wait(2)

        duplicate_status, _ = _request(app, "/jobs", VALID_JOB)

        assert 400 <= duplicate_status < 500
        assert app.snapshot()["accepted_total"] == 1
    finally:
        app.stop()


def test_unknown_path_returns_not_found(tmp_path):
    app = _start_app(tmp_path, lambda job: None)
    try:
        status, _ = _request(app, "/missing")

        assert status == 404
    finally:
        app.stop()


def test_single_worker_processes_accepted_jobs_in_fifo_order(tmp_path):
    order = []
    first_started = threading.Event()
    release_first = threading.Event()
    second_processed = threading.Event()

    def processor(job):
        order.append(job["job_id"])
        if job["job_id"] == "job-a":
            first_started.set()
            release_first.wait(2)
        else:
            second_processed.set()

    app = _start_app(tmp_path, processor)
    try:
        first = {**VALID_JOB, "job_id": "job-a"}
        second = {**VALID_JOB, "job_id": "job-b"}
        assert _request(app, "/jobs", first)[0] == 202
        assert first_started.wait(2)
        assert _request(app, "/jobs", second)[0] == 202
        release_first.set()

        assert second_processed.wait(2)
        assert order == ["job-a", "job-b"]
    finally:
        release_first.set()
        app.stop()


def test_configured_workers_can_process_jobs_concurrently(tmp_path):
    started = []
    lock = threading.Lock()
    both_started = threading.Event()
    release = threading.Event()

    def processor(job):
        with lock:
            started.append(job["job_id"])
            if len(started) == 2:
                both_started.set()
        release.wait(2)

    app = _start_app(tmp_path, processor, worker_count=2)
    try:
        assert _request(app, "/jobs", {**VALID_JOB, "job_id": "job-a"})[0] == 202
        assert _request(app, "/jobs", {**VALID_JOB, "job_id": "job-b"})[0] == 202

        assert both_started.wait(2)
        assert set(started) == {"job-a", "job-b"}
    finally:
        release.set()
        app.stop()


def test_shutdown_allows_an_active_job_to_finish(tmp_path):
    started = threading.Event()
    release = threading.Event()
    processed = threading.Event()
    stopped = threading.Event()

    def processor(job):
        started.set()
        release.wait(2)
        processed.set()

    app = _start_app(tmp_path, processor)
    assert _request(app, "/jobs", VALID_JOB)[0] == 202
    assert started.wait(2)

    shutdown = threading.Thread(target=lambda: (app.stop(), stopped.set()))
    shutdown.start()
    try:
        release.set()
        assert processed.wait(2)
        assert stopped.wait(2)
    finally:
        release.set()
        shutdown.join(timeout=2)
