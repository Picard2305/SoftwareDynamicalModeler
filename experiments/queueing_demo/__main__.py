import argparse
import signal
import threading

from experiments.queueing_demo.app import QueueingApp


def _argument_parser():
    parser = argparse.ArgumentParser(
        prog="python -m experiments.queueing_demo",
        description="Run the local in-memory queueing experiment application.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address; defaults to localhost")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port; use 0 to select an available port")
    parser.add_argument("--workers", type=int, default=1, help="number of job worker threads")
    parser.add_argument(
        "--cpu-work-units",
        type=int,
        default=100000,
        help="bounded CPU work units per cpu job",
    )
    parser.add_argument(
        "--sampling-interval",
        type=float,
        default=1.0,
        help="telemetry sampling interval in seconds",
    )
    parser.add_argument(
        "--drain-timeout",
        type=float,
        default=5.0,
        help="maximum worker drain time in seconds after shutdown",
    )
    parser.add_argument("--output-dir", required=True, help="new output directory for this app run")
    parser.add_argument("--run-id", help="experiment run ID shared with the k6 workload")
    return parser


def main(argv=None):
    parser = _argument_parser()
    args = parser.parse_args(argv)
    try:
        app = QueueingApp(
            host=args.host,
            port=args.port,
            worker_count=args.workers,
            cpu_work_units=args.cpu_work_units,
            drain_timeout_seconds=args.drain_timeout,
            output_dir=args.output_dir,
            run_id=args.run_id,
            sampling_interval_seconds=args.sampling_interval,
        )
        app.start()
    except (OSError, ValueError) as error:
        parser.exit(2, f"{parser.prog}: error: {error}\n")

    print(f"Listening on {app.base_url}", flush=True)
    print(f"Run ID: {app.run_id}", flush=True)
    print(f"Output directory: {args.output_dir}", flush=True)

    stop_requested = threading.Event()
    signal.signal(signal.SIGINT, lambda signum, frame: stop_requested.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda signum, frame: stop_requested.set())

    try:
        while not stop_requested.wait(0.25):
            pass
    finally:
        clean_shutdown = app.stop()

    return 0 if clean_shutdown else 1


if __name__ == "__main__":
    raise SystemExit(main())
