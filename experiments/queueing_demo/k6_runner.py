import argparse
import os
import subprocess
from pathlib import Path


def run_k6(
    target_url,
    run_id,
    output_dir,
    job_type="cpu",
    smoke=False,
    k6_version=None,
    k6_executable="k6",
):
    output_path = Path(output_dir).expanduser().resolve()
    try:
        output_path.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise FileExistsError(f"output directory already exists: {output_path}") from error

    environment = os.environ.copy()
    environment.update(
        {
            "TARGET_URL": target_url,
            "RUN_ID": run_id,
            "JOB_TYPE": job_type,
            "OUTPUT_DIR": str(output_path),
            "SMOKE": "1" if smoke else "0",
        }
    )
    if k6_version is not None:
        environment["K6_VERSION"] = k6_version

    script_path = Path(__file__).with_name("load.js").resolve()
    metrics_path = output_path / "metrics.json"
    result = subprocess.run(
        [k6_executable, "run", "--out", f"json={metrics_path}", str(script_path)],
        env=environment,
        check=False,
    )
    return result.returncode


def _argument_parser():
    parser = argparse.ArgumentParser(
        prog="python -m experiments.queueing_demo.k6_runner",
        description="Run the queueing-demo k6 workload in a new output directory.",
    )
    parser.add_argument("--target-url", required=True, help="complete local /jobs endpoint URL")
    parser.add_argument("--run-id", required=True, help="experiment run ID")
    parser.add_argument("--job-type", default="cpu", choices=("cpu",))
    parser.add_argument("--output-dir", required=True, help="new output directory for this k6 run")
    parser.add_argument("--k6-version", help="version label to include in the summary")
    parser.add_argument("--k6-executable", default="k6", help="k6 executable or absolute path")
    parser.add_argument("--smoke", action="store_true", help="use the short local smoke schedule")
    return parser


def main(argv=None):
    parser = _argument_parser()
    args = parser.parse_args(argv)
    try:
        return run_k6(
            target_url=args.target_url,
            run_id=args.run_id,
            output_dir=args.output_dir,
            job_type=args.job_type,
            smoke=args.smoke,
            k6_version=args.k6_version,
            k6_executable=args.k6_executable,
        )
    except (FileExistsError, OSError) as error:
        parser.exit(2, f"{parser.prog}: error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
