# Technical Specification: Local Queueing Experiment with k6

## 1. Purpose and research role

Build a small, reproducible local software experiment that creates application telemetry for analysis with SoftwareDynamicalModeler. A k6 script supplies a known, deterministic HTTP arrival-rate input. A minimal Python application accepts jobs into an in-memory queue, processes them with worker threads, and records queue/worker dynamics.

The first experiment is intended to study whether a known workload perturbation produces measurable queueing dynamics and whether those dynamics can be represented by a compact model. The queue implementation is deliberately transparent so that queue state, worker count, and service work are controlled. The result is a local baseline, not a claim that the application represents arbitrary production services.

## 2. Project context, scope, and exclusions

**Target project:** `C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler`  
**Experiment directory:** `experiments/queueing_demo/`

The repository has a Python `sdmodeler` CLI, pytest tests, top-level `examples/` and `tests/` directories, and a README. It has no configured lint or type-check command. Use existing project conventions and avoid changes to the existing fitting and validation pipeline.

This deliverable includes:

- a minimal local HTTP queueing application;
- an in-memory FIFO queue and configurable worker thread count;
- deterministic CPU job processing with bounded work;
- application-level telemetry written to CSV, plus run metadata;
- a Grafana k6 script for deterministic, open-model step arrivals;
- tests and instructions for a local end-to-end run.

This deliverable does **not** include RabbitMQ, Celery, AWS deployment, CloudWatch integration, production persistence, a distributed queue, arbitrary request payload templates, or a custom load-generator implementation/CLI. Those are possible later experimental stages, each to be scoped separately. Do not add a dependency to or modify the `sdmodeler` package, CLI, or model-fitting workflow.

Use Python standard-library facilities for the application, HTTP server, queue, threads, timestamps, and CSV/JSON output. k6 is a separate external prerequisite for generating HTTP load; do not install it automatically. Record the k6 version used for verification. Do not add runtime Python dependencies without an explicit need and an updated specification.

## 3. Architecture

```text
k6 open-model arrival-rate scenarios
                 |
                 | HTTP POST /jobs (one request per iteration)
                 v
        Python local HTTP app
                 |
                 v
       in-memory FIFO queue
                 |
                 v
         worker thread pool
            |           |
            +-----------+----> application telemetry CSV + run metadata
                                      |
                                      v
                           SoftwareDynamicalModeler (manual analysis input)
```

The generator runs independently from the application. k6 schedules iteration starts based on its open-model executor, not on the previous HTTP response. Each iteration must make exactly one POST. Do not use closed-model virtual users, request loops inside an iteration, or sleeps to pace traffic.

The app is one local process with an in-memory queue. One process hosts the HTTP server, queue, telemetry sampler, and worker threads. This avoids broker and network topology as additional variables in the baseline. The workload generator and app may run on the same development machine for the local prototype; document that generator resource use can affect measured host metrics and should be isolated in later system-level experiments.

## 4. Workload contract (k6)

### 4.1 Schedule

Use a deterministic, piecewise-constant step schedule with no jitter and no random seed. The illustrative schedule is:

| Offset | Duration | Target arrival rate |
|---|---:|---:|
| 0 s | 60 s | 5 jobs/s |
| 60 s | 60 s | 20 jobs/s |
| 120 s | 60 s | 50 jobs/s |
| 180 s | 60 s | 10 jobs/s |

Make the target URL, run ID, and job type configurable through k6 environment inputs. Do not hard-code a target host. Use k6's open-model arrival-rate scenario(s), with one job POST per iteration. Prefer separate constant-arrival scenarios with explicit start offsets/durations for the step plateaus, provided the chosen k6 version executes them without unintended overlap or gaps. Verify and document k6's boundary behavior; do not implement a custom scheduler to reproduce the earlier custom-generator phase contract.

Configure `preAllocatedVUs` and `maxVUs` so the selected schedule has enough execution capacity. Capture k6's `dropped_iterations` metric. If it is nonzero, mark the run as not having achieved the target offered load and explain that drops may reflect insufficient generator VUs or slow target responses. Do not present the configured target rate as the achieved request rate without checking k6 results.

### 4.2 Request body

Send `Content-Type: application/json` and a fixed JSON body containing:

- `run_id`: unique identifier for the experiment run;
- `job_id`: unique identifier for the job, unique across VUs and scenarios;
- `submitted_at`: actual UTC wall-clock timestamp when the k6 iteration submits the request;
- `job_type`: supported work type, initially `cpu`.

Use a k6 execution-context field verified to provide a unique per-iteration ID for `job_id`. `submitted_at` is the actual request-submission timestamp, not a reconstructed target-slot timestamp. Do not claim the script exports a per-request ideal schedule timestamp unless k6 provides one directly. Do not add arbitrary JSON templates or random payload generation.

Optional headers may be supported for local configuration, but no credentials are required for the example. Header values must not be logged or persisted. Do not log response bodies. Do not allow credentials in URL user information.

### 4.3 k6 output

Produce timestamped k6 metrics (JSON output) and an end-of-run machine-readable summary. Include or preserve run ID, schedule, job type, k6 version, iteration/request counts, HTTP outcomes, latency statistics, and `dropped_iterations` so the run can be compared with app telemetry. Use a unique run output directory or unique filenames; never silently overwrite prior run results. Keep generated outputs outside source control by default.

## 5. Queueing application contract

### 5.1 HTTP endpoints

- `POST /jobs`: validate and accept a job into the in-memory queue; return an accepted response (`202`) containing the `job_id`. The HTTP response acknowledges enqueueing and does not wait for job completion.
- `GET /health`: optional lightweight readiness response so the user can confirm the local server is ready before starting k6.
- Unknown paths return `404`; malformed JSON or missing/invalid fields return `400`; unsupported job types return a clear client error. The server must not return internal stack traces to the client.

Use the standard library's HTTP server unless repository conventions indicate an existing server framework that is already a declared dependency. Bind to localhost by default. Require an explicit bind override before exposing the experimental server to other hosts.

### 5.2 Queue and workers

Use a standard-library FIFO queue and a configurable positive worker count, defaulting to one. Jobs are accepted in request order and processed by available workers. The queue is in memory: process shutdown loses queued jobs; durability and retries are outside scope.

The initial `cpu` job performs a deterministic, bounded amount of CPU work. Configure workload size on the app (not with arbitrary per-request payload fields), and provide a safe local default. Keep the processing function separable/injectable so tests can use a fast deterministic stub rather than rely on timing-sensitive CPU assertions.

At shutdown, stop accepting new work, signal workers, and apply a documented bounded drain policy. Telemetry files must be finalized with a valid schema even if shutdown is interrupted, where practical.

## 6. Telemetry model and conservation accounting

Write app telemetry at a documented fixed sampling interval, configurable at app startup. Use UTC wall-clock timestamps for rows and monotonic time for elapsed durations and latency measurement. Take related counters and queue sizes from a consistent snapshot/lock where practical.

Each telemetry row must include at least:

- `timestamp_utc`;
- `accepted_total` (cumulative accepted jobs);
- `completed_total` (cumulative completed jobs);
- `waiting_depth` (queued but not currently executing);
- `active_jobs` (jobs currently executing);
- `unfinished_backlog` (`waiting_depth + active_jobs`);
- `completed_since_previous_sample`;
- completed-job latency count and a documented summary, when completions occurred during that interval.

The app must maintain the accounting invariant:

```text
unfinished_backlog = accepted_total - completed_total
```

assuming no rejection after acceptance, cancellation, or process restart within one run. Record accepted request counters only after successful enqueueing. Document any brief sampler boundary effects if a fully atomic snapshot is not possible.

The reference balance equation applies to **total unfinished backlog**:

```text
d(unfinished_backlog)/dt ≈ accepted_arrival_rate - completion_rate
```

For `waiting_depth` alone, outflow is jobs starting service, not job completions. Do not describe the latter equation as a queue-length equation unless its state definition uses that service-start outflow. This distinction must appear in the experiment documentation.

Do not require host CPU or memory metrics in the baseline. They can be added later through a separately specified, portable OS metrics source or CloudWatch when moving the experiment to AWS. The baseline should first validate the queue and worker dynamics with app-level telemetry.

### 6.1 App run metadata and outputs

For each run, write app telemetry CSV and `run_metadata.json` under an explicit output directory. Metadata should include run ID, app start/end UTC, sampling interval, worker count, CPU work configuration, app version if available, row count, final accepted/completed/backlog counts, and whether shutdown was clean. Do not store request headers, secrets, or response bodies.

Refuse to overwrite an existing run directory by default. If an overwrite option is implemented, replace only the app's named output artifacts and preserve unrelated directory contents; do not recursively delete the output directory.

Document that k6's timestamped output and app CSV correlate via `run_id`, `job_id`, and actual submission/acceptance timestamps. The experiment does not automatically convert these files into the canonical `sdmodeler` input schema; users can prepare an analysis CSV with the existing project pipeline.

## 7. Proposed source and test structure

Keep experiment code outside `sdmodeler`:

- `experiments/__init__.py` and `experiments/queueing_demo/__init__.py` as needed for the Python module entry point;
- `experiments/queueing_demo/app.py` (or small focused modules) for HTTP endpoints, queue, workers, and app lifecycle;
- `experiments/queueing_demo/telemetry.py` for sampling and CSV/run-metadata output;
- `experiments/queueing_demo/load.js` for the k6 open-model step workload;
- `experiments/queueing_demo/example_config.json` or documented environment variables for non-secret local settings;
- tests under `tests/` for app, queue/worker behavior, telemetry accounting/schema, and k6 script contract where feasible;
- `experiments/queueing_demo/README.md` or the project README for setup and experiment instructions.

Do not add a Python load generator, workload scheduler, or `workloadgen` package. Do not add RabbitMQ, Celery, AWS code, or generated run outputs to the source tree in this deliverable.

## 8. Testing and verification

Use focused tests for Python application behavior and telemetry. Keep tests offline and deterministic. Test request validation/acceptance, queue and worker accounting, bounded job processing interface, shutdown, telemetry schema, the backlog invariant, unique run/job correlation, output collision behavior, and partial artifact validity. Inject the processing function and clock/sampler where needed; avoid flaky tests based on exact wall-clock durations.

For k6, add no dependency to pytest. Validate the script with the chosen k6 version when available, then run a short local end-to-end smoke test against the sample app. The smoke test must use localhost only and verify that each iteration creates one accepted job, app telemetry changes, timestamps/IDs correlate, and `dropped_iterations` is visible and zero for the chosen low-rate smoke schedule. If k6 is not installed, do not install it automatically; report that the external smoke test could not run and provide the exact command for the user to run after installing k6.

Run focused pytest tests during development and the full documented `pytest` suite after implementation. Manually verify app startup/shutdown, k6 command, output files, and that no secret or response body is written. No lint/type-check command is configured; report this rather than introducing an unrelated tool.

## 9. Documentation and limitations

Document prerequisites (Python and k6), separate commands to start the app and run k6, configuration/environment inputs, output schemas, the step schedule, how to identify dropped iterations, and how to align app telemetry with the input workload. Include the workload generator/app separation and recommend running the generator on a separate host for later system resource studies.

State these limits plainly:

1. The baseline is a single-process, in-memory queue, not a durable or distributed production queue.
2. The synthetic CPU task is controlled and does not represent all real application work.
3. k6 arrival-rate execution requires enough VUs; dropped iterations mean the offered schedule was not fully delivered.
4. App-level queue telemetry is not CloudWatch telemetry, and no AWS integration is included.
5. No automatic fitting, ingestion, or `sdmodeler` integration is added.
6. RabbitMQ, Celery, production workloads, and deployment to AWS are future experiments requiring separate review.

## 10. Review checkpoint

This specification and its implementation plan are presented for review before any application source or tests are changed. Implementation begins only after the user approves both artifacts.
