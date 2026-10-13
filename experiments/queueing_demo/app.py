import json
import math
import queue
import statistics
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from experiments.queueing_demo.telemetry import RunArtifacts


_REQUIRED_JOB_FIELDS = {"run_id", "job_id", "submitted_at", "job_type"}
_MAX_REQUEST_BYTES = 65536
_MAX_CPU_WORK_UNITS = 1000000


def _utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _QueueingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True


class _RequestHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def do_POST(self):
        if self.path != "/jobs":
            self._send_json(404, {"error": "not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(400, {"error": "invalid content length"})
            return

        if content_length < 0 or content_length > _MAX_REQUEST_BYTES:
            self._send_json(400, {"error": "invalid request size"})
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
            job = self.server.app.validate_job(payload)
        except (UnicodeDecodeError, ValueError, TypeError):
            self._send_json(400, {"error": "invalid job request"})
            return

        try:
            self.server.app.enqueue(job)
        except ValueError:
            self._send_json(400, {"error": "invalid job request"})
            return
        except RuntimeError:
            self._send_json(503, {"error": "application is shutting down"})
            return

        self._send_json(202, {"job_id": job["job_id"]})

    def do_GET(self):
        self._send_json(404, {"error": "not found"})

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        return


class QueueingApp:
    def __init__(
        self,
        host="127.0.0.1",
        port=8000,
        worker_count=1,
        processor=None,
        cpu_work_units=100000,
        drain_timeout_seconds=5.0,
        output_dir=None,
        run_id=None,
        sampling_interval_seconds=1.0,
    ):
        if not isinstance(host, str) or not host:
            raise ValueError("host must be a non-empty string")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("port must be an integer between 0 and 65535")
        if isinstance(worker_count, bool) or not isinstance(worker_count, int) or worker_count < 1:
            raise ValueError("worker_count must be a positive integer")
        if isinstance(cpu_work_units, bool) or not isinstance(cpu_work_units, int):
            raise ValueError("cpu_work_units must be a nonnegative integer")
        if not 0 <= cpu_work_units <= _MAX_CPU_WORK_UNITS:
            raise ValueError("cpu_work_units is outside the supported range")
        if not isinstance(drain_timeout_seconds, (int, float)) or isinstance(drain_timeout_seconds, bool):
            raise ValueError("drain_timeout_seconds must be a nonnegative number")
        if not math.isfinite(drain_timeout_seconds) or drain_timeout_seconds < 0:
            raise ValueError("drain_timeout_seconds must be a nonnegative finite number")
        if processor is not None and not callable(processor):
            raise ValueError("processor must be callable")
        if output_dir is None:
            raise ValueError("output_dir is required")
        if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
            raise ValueError("run_id must be a non-empty string")
        if not isinstance(sampling_interval_seconds, (int, float)) or isinstance(
            sampling_interval_seconds, bool
        ):
            raise ValueError("sampling_interval_seconds must be a positive number")
        if not math.isfinite(sampling_interval_seconds) or sampling_interval_seconds <= 0:
            raise ValueError("sampling_interval_seconds must be a positive finite number")

        self.host = host
        self.port = port
        self.worker_count = worker_count
        self.cpu_work_units = cpu_work_units
        self.drain_timeout_seconds = float(drain_timeout_seconds)
        self.output_dir = output_dir
        self.run_id = run_id or uuid.uuid4().hex
        self.sampling_interval_seconds = float(sampling_interval_seconds)
        self._processor = processor if processor is not None else self._process_cpu_job
        self._jobs = queue.Queue()
        self._state_lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self._accepting = False
        self._server = None
        self._server_thread = None
        self._workers = []
        self._sampler_thread = None
        self._sampler_stop = threading.Event()
        self._artifacts = None
        self._started_once = False
        self._clean_shutdown = True
        self._waiting_depth = 0
        self._active_jobs = 0
        self._accepted_total = 0
        self._completed_total = 0
        self._worker_errors = 0
        self._job_records = {}
        self._pending_latencies = []
        self._start_time_utc = None
        self.base_url = None

    def start(self):
        with self._state_lock:
            if self._started_once:
                raise RuntimeError("application can only be started once")
            server = _QueueingHTTPServer((self.host, self.port), _RequestHandler)
            try:
                artifacts = RunArtifacts(self.output_dir)
            except BaseException:
                server.server_close()
                raise

            server.app = self
            self._server = server
            self._artifacts = artifacts
            self._started_once = True
            self._start_time_utc = _utc_timestamp()
            self.port = server.server_address[1]
            display_host = f"[{self.host}]" if ":" in self.host else self.host
            self.base_url = f"http://{display_host}:{self.port}"
            self._accepting = True
            self._workers = [
                threading.Thread(
                    target=self._worker_loop,
                    name=f"queueing-worker-{index + 1}",
                    daemon=True,
                )
                for index in range(self.worker_count)
            ]
            for worker in self._workers:
                worker.start()
            self._server_thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.05},
                name="queueing-http-server",
                daemon=True,
            )
            self._server_thread.start()
            self._record_sample()
            self._sampler_thread = threading.Thread(
                target=self._sampling_loop,
                name="queueing-telemetry-sampler",
                daemon=True,
            )
            self._sampler_thread.start()
        return self

    def stop(self):
        with self._stop_lock:
            with self._state_lock:
                server = self._server
                if server is None:
                    return self._clean_shutdown
                self._accepting = False
                server_thread = self._server_thread
                workers = tuple(self._workers)
                sampler_thread = self._sampler_thread

            server.shutdown()
            server.server_close()
            if server_thread is not None:
                server_thread.join(timeout=self.drain_timeout_seconds)
            clean_server = server_thread is None or not server_thread.is_alive()

            for _ in workers:
                self._jobs.put(None)

            deadline = time.monotonic() + self.drain_timeout_seconds
            for worker in workers:
                worker.join(timeout=max(0.0, deadline - time.monotonic()))
            clean_workers = all(not worker.is_alive() for worker in workers)

            self._sampler_stop.set()
            if sampler_thread is not None:
                sampler_thread.join(timeout=max(1.0, self.sampling_interval_seconds * 2))
            clean_sampler = sampler_thread is None or not sampler_thread.is_alive()

            self._record_sample()
            with self._state_lock:
                end_time_utc = _utc_timestamp()
                clean_shutdown = clean_server and clean_workers and clean_sampler
                self._clean_shutdown = clean_shutdown
                snapshot = self._snapshot_locked()
                jobs = []
                for job_record in self._job_records.values():
                    job = dict(job_record)
                    if job["result"] == "accepted":
                        job["result"] = "unfinished"
                    jobs.append(job)
                metadata = {
                    "run_id": self.run_id,
                    "start_time_utc": self._start_time_utc,
                    "end_time_utc": end_time_utc,
                    "sampling_interval_seconds": self.sampling_interval_seconds,
                    "worker_count": self.worker_count,
                    "cpu_work_units": self.cpu_work_units,
                    "app_version": None,
                    "accepted_total": snapshot["accepted_total"],
                    "completed_total": snapshot["completed_total"],
                    "unfinished_backlog": snapshot["unfinished_backlog"],
                    "worker_errors": snapshot["worker_errors"],
                    "clean_shutdown": clean_shutdown,
                }
                artifacts = self._artifacts
                self._server = None
                self._server_thread = None
                self._workers = []
                self._sampler_thread = None
                self._artifacts = None

            artifacts.finalize(jobs, metadata)
            return clean_shutdown

    def validate_job(self, payload):
        if not isinstance(payload, dict) or set(payload) != _REQUIRED_JOB_FIELDS:
            raise ValueError("job must contain the supported fields")

        for field in ("run_id", "job_id", "submitted_at", "job_type"):
            if not isinstance(payload[field], str) or not payload[field].strip():
                raise ValueError("job fields must be non-empty strings")

        if payload["run_id"] != self.run_id:
            raise ValueError("job run ID does not match the active run")
        if payload["job_type"] != "cpu":
            raise ValueError("unsupported job type")

        timestamp = payload["submitted_at"]
        if timestamp.endswith("Z"):
            timestamp = f"{timestamp[:-1]}+00:00"
        try:
            submitted_at = datetime.fromisoformat(timestamp)
        except ValueError as error:
            raise ValueError("submitted_at must be a UTC timestamp") from error
        if submitted_at.tzinfo is None or submitted_at.utcoffset() != timedelta(0):
            raise ValueError("submitted_at must be a UTC timestamp")

        return dict(payload)

    def enqueue(self, job):
        accepted_at_utc = _utc_timestamp()
        accepted_at_monotonic = time.monotonic()
        with self._state_lock:
            if not self._accepting:
                raise RuntimeError("application is not accepting jobs")
            if job["job_id"] in self._job_records:
                raise ValueError("job_id must be unique within a run")
            record = {
                "run_id": job["run_id"],
                "job_id": job["job_id"],
                "submitted_at": job["submitted_at"],
                "accepted_at_utc": accepted_at_utc,
                "completed_at_utc": "",
                "latency_seconds": "",
                "result": "accepted",
            }
            self._jobs.put((job, accepted_at_monotonic))
            self._job_records[job["job_id"]] = record
            self._waiting_depth += 1
            self._accepted_total += 1

    def snapshot(self):
        with self._state_lock:
            return self._snapshot_locked()

    def _snapshot_locked(self):
        return {
            "accepted_total": self._accepted_total,
            "completed_total": self._completed_total,
            "waiting_depth": self._waiting_depth,
            "active_jobs": self._active_jobs,
            "unfinished_backlog": self._waiting_depth + self._active_jobs,
            "worker_errors": self._worker_errors,
        }

    def _sampling_loop(self):
        next_sample = time.monotonic() + self.sampling_interval_seconds
        while not self._sampler_stop.is_set():
            remaining = max(0.0, next_sample - time.monotonic())
            if self._sampler_stop.wait(remaining):
                return
            self._record_sample()
            current_time = time.monotonic()
            next_sample += self.sampling_interval_seconds
            if next_sample <= current_time:
                next_sample = current_time + self.sampling_interval_seconds

    def _record_sample(self):
        with self._state_lock:
            latencies = self._pending_latencies
            self._pending_latencies = []
            snapshot = self._snapshot_locked()
            sample = {
                "timestamp_utc": _utc_timestamp(),
                **snapshot,
                "completed_since_previous_sample": len(latencies),
                "completed_job_latency_count": len(latencies),
                "completed_latency_mean_seconds": statistics.fmean(latencies) if latencies else "",
                "completed_latency_median_seconds": statistics.median(latencies) if latencies else "",
                "completed_latency_p95_seconds": self._percentile_95(latencies),
                "completed_latency_max_seconds": max(latencies) if latencies else "",
            }
            artifacts = self._artifacts
        if artifacts is not None:
            artifacts.record_sample(sample)

    def _worker_loop(self):
        while True:
            item = self._jobs.get()
            if item is None:
                self._jobs.task_done()
                return

            job, accepted_at_monotonic = item
            with self._state_lock:
                self._waiting_depth -= 1
                self._active_jobs += 1

            failed = False
            try:
                self._processor(job)
            except Exception:
                failed = True
            finally:
                completed_at_monotonic = time.monotonic()
                latency_seconds = completed_at_monotonic - accepted_at_monotonic
                with self._state_lock:
                    self._active_jobs -= 1
                    self._completed_total += 1
                    if failed:
                        self._worker_errors += 1
                    self._pending_latencies.append(latency_seconds)
                    record = self._job_records[job["job_id"]]
                    record["completed_at_utc"] = _utc_timestamp()
                    record["latency_seconds"] = latency_seconds
                    record["result"] = "processing_error" if failed else "success"
                self._jobs.task_done()

    @staticmethod
    def _percentile_95(values):
        if not values:
            return ""
        ordered = sorted(values)
        index = max(0, math.ceil(0.95 * len(ordered)) - 1)
        return ordered[index]

    def _process_cpu_job(self, job):
        state = 0
        for unit in range(self.cpu_work_units):
            state = (state * 31 + unit) % 1000003
        return state
