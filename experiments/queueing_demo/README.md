# Local queueing experiment

This experiment combines a small local HTTP queueing application and a k6 workload script. It is separate from `sdmodeler`: it does not fit models, transform telemetry into the canonical analysis schema, or change the identification pipeline. It includes no external receiver, AWS integration, RabbitMQ, or Celery.

## Prerequisites

- Python 3.8 or later; the app and its tests use only the Python standard library and the repository's existing pytest setup.
- Grafana k6 v0.34 or later for the `k6/execution` module used to create per-iteration job IDs. k6 is an external executable and is not installed by the Python project.

Check the installed versions with `python --version` and `k6 version`. Run Python tests from the repository root with `python -m pytest`.

## Start the local app

Choose one run ID and a new output directory. Start the app in a terminal and leave it running while k6 runs:

```powershell
$runId = "queue-demo-local-001"
$runRoot = ".\runs\$runId"
python -m experiments.queueing_demo --host 127.0.0.1 --port 8000 --workers 1 --cpu-work-units 100000 --sampling-interval 1 --drain-timeout 5 --run-id $runId --output-dir "$runRoot\app"
```

The default bind is `127.0.0.1`. Keep the app local for this experiment; an explicit host override can expose this experimental server to other machines. `POST /jobs` validates a job, places it in a process-local FIFO queue, and returns HTTP `202` with its job ID. The response acknowledges enqueueing, not completion. Unknown paths return `404`; malformed, duplicate, or unsupported jobs are rejected. The only supported job type is `cpu`.

The worker count and CPU work size are app settings, not request payload controls. Processing is deterministic and bounded by the app's supported CPU-work limit. The queue is in memory, so process termination loses jobs that have not completed. Ctrl+C stops accepting new work, waits up to the configured drain timeout, and writes final artifacts. Jobs still in progress at that deadline are recorded as unfinished; a non-clean shutdown is reflected in the metadata.

## Run the k6 workload

In a second PowerShell terminal, use the same run ID and a new output directory that does not already exist. The short smoke schedule is one iteration per second for five seconds:

```powershell
$runId = "queue-demo-local-001"
$runRoot = ".\runs\$runId"
$k6Version = (k6 version | Select-Object -First 1)
python -m experiments.queueing_demo.k6_runner --target-url "http://127.0.0.1:8000/jobs" --run-id $runId --job-type cpu --output-dir "$runRoot\k6" --k6-version $k6Version --smoke
```

The launcher creates the output directory exclusively before starting k6. If that directory already exists, it stops without changing its contents; choose a fresh path for every run. Do not create the k6 output directory beforehand. After a smoke run, stop the app with Ctrl+C. To run the full four-minute step schedule instead, omit `--smoke`. It uses four `constant-arrival-rate` scenarios: 5 jobs/s for 60 seconds, then 20 jobs/s, 50 jobs/s, and 10 jobs/s for 60 seconds each. Each iteration makes one `POST /jobs`; there is no `sleep()` or closed-model pacing. Arrival-rate iterations are started independently of HTTP response times, as documented by [the k6 constant-arrival-rate executor](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/). k6 spaces starts fractionally and runs the scenarios at their configured offsets; this is not a promise that requests land on an exact wall-clock boundary.

The launcher passes `TARGET_URL`, `RUN_ID`, `JOB_TYPE`, and `OUTPUT_DIR` to k6. The URL must point to the app's `/jobs` endpoint and must not contain URL credentials. The script does not log header values or response bodies. Use a new output directory for each run; the launcher refuses to reuse one so k6 cannot overwrite prior results.

The script captures `dropped_iterations`. If it is nonzero, k6 could not start every scheduled iteration, so the configured offered load was not fully delivered. A drop can indicate that the generator lacks available VUs or that iterations are taking too long. The script's preallocated and maximum VU counts are starting capacity settings, not proof that the offered rate was achieved. Interpret the measured k6 iteration and request metrics and the dropped count together; HTTP failures and transport errors are reported separately.

## Output files and correlation

The app writes into its new `--output-dir`:

- `telemetry.csv`: fixed-interval samples with UTC timestamp, accepted/completed totals, waiting depth, active jobs, unfinished backlog, completions since the prior sample, and completed-job latency count/summary.
- `jobs.csv`: one row per accepted job with `run_id`, `job_id`, submitted and accepted UTC timestamps, completion timestamp and latency when available, and a result category (`success`, `processing_error`, or `unfinished`).
- `run_metadata.json`: run ID, app start/end UTC, sampling interval, worker count, CPU work setting, sample row count, final accepted/completed/backlog counts, worker errors, and clean-shutdown status.

The k6 run writes timestamped metric samples to `k6/metrics.json` and an end-of-run `k6/k6-summary.json` containing the run ID, workload schedule, completed iterations and HTTP requests, explicit request-attempt start/completion counters, accepted/non-accepted/transport outcomes, HTTP latency statistics, k6 version if supplied, and dropped-iteration count. A request attempt is counted as completed when the synchronous k6 HTTP call returns, including a transport-error response. Keep each output directory with its run; the app refuses to use an existing run directory.

Join the workload and app records using `run_id` and `job_id`. k6's `submitted_at` is the actual UTC timestamp created at the iteration's request submission. The app records `accepted_at_utc` and `completed_at_utc`; its periodic CSV describes aggregate queue state. These are wall-clock timestamps for correlation, while the app uses a monotonic clock for measured durations. The files are not automatically converted into `sdmodeler` input; prepare an analysis dataset separately using the existing project pipeline.

## Interpreting queue telemetry

The app records `waiting_depth` as jobs queued but not executing, `active_jobs` as work currently executing, and `unfinished_backlog` as their sum. Within one run, absent cancellation or restart, the accounting invariant is:

```text
unfinished_backlog = accepted_total - completed_total
```

The total unfinished-backlog balance is approximately driven by accepted arrival rate minus completion rate. Do not apply that same outflow interpretation to `waiting_depth` alone: waiting jobs leave that state when service starts, not when service completes. Sampling is periodic, so CSV rows are observations rather than a continuous event log.

## Limitations and next research stages

- This is a local single-process in-memory queue, not a durable or distributed production queue.
- The synthetic CPU task is reproducible and bounded but does not represent every application's work.
- Offered load depends on the capacity of the k6 host and its configured VUs. Dropped iterations make load shortfall visible; run k6 on a separate host for later system-resource experiments.
- App-level telemetry is not CloudWatch telemetry. No AWS deployment or integration is included.
- There is no automatic telemetry ingestion, fitting, or other `sdmodeler` integration.
- RabbitMQ, Celery, production workloads, and AWS deployment are future experiments, not components of this baseline. Change one factor at a time: first move the same app to AWS while retaining the in-memory queue, then evaluate RabbitMQ, and introduce Celery only if framework effects are a research question.
