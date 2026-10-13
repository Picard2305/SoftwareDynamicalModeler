# Implementation Plan: Local Queueing Experiment with k6

**Target project:** `C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler`  
**Specification:** `.zencoder/chats/3f025934-f50a-47b2-a15a-206c57c10e52/spec.md`  
**Execution status:** Specification and plan approved; Step 1 complete. Implementation may proceed.

## Goal and scope

Build a small, runnable **local experiment** that uses k6 to apply a controlled HTTP workload to a simple Python queueing application. The application accepts jobs, places them in an in-memory queue, processes them with worker threads, and records application telemetry. This supplies the sample software and telemetry needed to exercise SoftwareDynamicalModeler end to end without AWS.

Keep all experiment assets under `experiments/queueing_demo/`, separate from `sdmodeler`. Use a k6 open-model arrival-rate scenario for the deterministic step workload; each iteration sends exactly one job request. Use the Python standard library for the app, queue, workers, and telemetry unless repository conventions establish a compelling reason otherwise. Do not implement a custom load generator, custom load-generator CLI, RabbitMQ, Celery, CloudWatch integration, or changes to the model-fitting pipeline in this first experiment.

The queue is deliberately in-memory and single-process. The initial objective is interpretability and a reproducible local baseline, not production durability, multi-host scaling, or a general-purpose task system. Later experiments may replace the in-memory queue with RabbitMQ, add Celery, or move the same application to AWS; each should be a separate stage that changes one architectural factor at a time.

**Specification alignment:** the linked `spec.md` now describes the confirmed local app, telemetry, and k6 scope, superseding the earlier generator-only request. Review both artifacts with the user and resolve any remaining specification issues before implementation; this plan does not authorize source changes by itself.

## Experimental and telemetry contracts

- **Workload:** deterministic piecewise-constant arrival-rate steps, with no jitter or seed. Use k6's open arrival-rate executor. One iteration makes one HTTP POST; never add sleeps that make request generation depend on previous responses.
- **Request:** fixed JSON fields for `run_id`, unique `job_id`, actual `submitted_at` UTC time, and supported `job_type` (initially `cpu`). Do not claim `submitted_at` is the planned k6 slot time; it is the actual submission timestamp. Do not add arbitrary payload templates.
- **Application:** `POST /jobs` accepts and enqueues a valid job, returning an acceptance response and job ID. A small `GET /health` endpoint may be included for local readiness checks. The receiver rejects malformed or unsupported job types with a clear client error.
- **Queue state:** record both waiting queue depth and active/in-service jobs. Define total unfinished backlog as `waiting + active`. For this backlog, the reference conservation relation is `change in unfinished backlog = accepted arrivals - completed jobs` (subject to the app's event accounting). If analyzing waiting-only depth, its outflow is jobs starting service, not job completions.
- **Telemetry:** at a documented fixed interval, record UTC timestamp, accepted-arrival cumulative count, completion cumulative count, waiting depth, active job count, unfinished backlog, and completed-job latency summary/count where available. Record enough metadata to correlate the app CSV with k6 outputs. Host CPU/memory collection is optional only if it can be implemented portably without obscuring the queue baseline; otherwise defer it to a later AWS/host-metrics stage.
- **k6 capacity:** configure `preAllocatedVUs` and `maxVUs`, preserve k6 `dropped_iterations`, and flag any run with dropped iterations as not having achieved the requested offered load. Do not equate target rate with achieved request rate.
- **Privacy:** no credentials are needed for the local example. If optional headers are supported, values must not be logged or written to result artifacts. Do not log request/response bodies beyond the minimal validation error needed by local users.

## Milestones

### [x] Step 1: Review and approve the experiment specification

**Dependencies:** The user confirmed that the local app + telemetry + k6 scope supersedes the earlier generator-only request and approved the final specification and plan.

Review `spec.md` for the local queueing app, app-level telemetry, and k6 workload contracts: app lifecycle, request/response behavior, `cpu` job processing, worker-count configuration, sampling interval, telemetry fields, run/job correlation, outputs, backlog equation, waiting-queue versus unfinished-backlog outflow, and local commands. Confirm that RabbitMQ, Celery, AWS/CloudWatch, and host CPU/memory telemetry remain deferred.

Review the open-model k6 schedule and documented k6 start/boundary semantics. Record supported k6 prerequisites without installing them implicitly. Correct any remaining inconsistency in the spec or this plan, then present both for explicit approval. Do not change application source or tests before that approval.

**Deliverable:** approved, internally consistent `spec.md` and this plan before application source/tests change.

### [x] Step 2: Implement the queueing app and worker lifecycle

**Dependencies:** Step 1 approval.

Implement a minimal HTTP receiver, in-memory FIFO queue, configurable small worker pool, and graceful start/stop lifecycle. A `cpu` job should perform a deterministic, configurable amount of bounded computation so service work is reproducible; keep the computation small enough for safe local tests and demonstrations. Make processing injectable or otherwise testable without asserting wall-clock completion times.

Write focused tests before each behavior, run the focused tests to establish failure, implement, and rerun/refactor. Test acceptance, malformed requests, unique IDs, queueing, worker processing, worker-count behavior, and shutdown without external services.

**Expected changes:** `experiments/queueing_demo/app.py` and supporting app/worker modules if separation improves clarity; focused tests under `tests/`.

### [x] Step 3: Add application telemetry and run artifacts

**Dependencies:** Step 2.

Add a fixed-interval telemetry sampler and CSV output for the agreed fields: UTC timestamp, cumulative accepted/completed counts, waiting depth, active count, unfinished backlog, and latency summary/count. Record run metadata such as run ID, app configuration, worker count, sampling interval, start/end times, and software version when available. Keep generated run data outside source control and prevent accidental overwrite of an existing run directory.

Test schema, timestamp ordering, count/backlog invariants, shutdown/partial output behavior, and correlation IDs. Avoid flaky timing assertions; inject the clock/sampler or validate invariants over controlled queue events.

**Expected changes:** `experiments/queueing_demo/telemetry.py` (or equivalent), output-path handling, focused tests, and a documented sample output schema.

### [x] Step 4: Add the k6 deterministic step workload

**Dependencies:** Steps 1–3.

**Verification note:** The script contract tests and Node syntax check pass, but k6 is unavailable; scenario boundary and output behavior have not been runtime-verified.

Create `experiments/queueing_demo/load.js` using an open-model arrival-rate executor. Encode the example as stepwise constant rates (for example 5, 20, 50, then 10 jobs/s) with explicit scenario start times/durations and exactly one `POST /jobs` per iteration. Use environment inputs for target URL, run ID, and job type; do not hard-code a real host or credentials. Use a unique job ID from a supported k6 execution-context field and verify that it is unique across VUs and scenarios.

Use `submitted_at` as the actual UTC request-submission time, not a reconstructed scheduled time. Do not use `sleep()` or closed-model VUs. Capture timestamped k6 JSON output and a machine-readable summary, including `dropped_iterations`, request starts/completions, HTTP failures, and the schedule metadata. Use unique output paths to avoid overwrites. Never write header values or response bodies to output.

Verify the documented k6 scenario semantics and output behavior against the chosen k6 version. If exact stage-boundary rates cannot be represented without a ramp, record the actual k6 behavior and adjust the experiment description; do not build a custom scheduler to imitate prior custom-generator phase semantics.

**Expected changes:** `experiments/queueing_demo/load.js`, a non-secret example configuration if useful, and focused script documentation. No Python load-generator module or `workloadgen` package.

### [x] Step 5: Local end-to-end smoke run

**Dependencies:** Steps 2–4.

**Verification note:** The Python-side local HTTP/telemetry behavior is exercised by the integration tests. `k6 version` is unavailable in this environment, so a k6-driven smoke run and dropped-iteration observation remain unverified; the documented fallback command is in `experiments/queueing_demo/README.md`.

Run the sample app locally and drive a short, low-rate k6 schedule against `localhost`. Verify the acceptance endpoint, job processing, telemetry growth, timestamp/correlation fields, and k6 request/result outputs. Introduce a controlled slower job to confirm that open-model iteration starts do not wait for prior HTTP responses. Check `dropped_iterations`; any nonzero count must appear as a run-quality failure/limitation.

The smoke test must contact only the local sample app, not AWS or any external target. If k6 is not installed, do not install it without authorization; report the blocker and provide the exact user-run command. Use a unique temporary output directory and confirm generated files are not committed as source fixtures unless intentionally curated.

### [x] Step 6: Document usage, assumptions, and later research stages

**Dependencies:** Steps 1–5.

**Verification note:** Documentation, the full pytest suite, CLI help, and JavaScript syntax checks are complete. Runtime verification of the k6 command, generated k6 outputs, and dropped iterations remains blocked until k6 is installed.

Update the README or add `experiments/queueing_demo/README.md` with prerequisites, commands to start the app and workers, the k6 command/configuration, output schemas, telemetry correlation, pacing semantics, dropped-iteration interpretation, shutdown behavior, and limitations. Explain the backlog conservation equation and the distinction between waiting and unfinished work.

Present RabbitMQ, Celery, and AWS/CloudWatch as possible future stages, not implemented components. Recommend changing one factor at a time (local app to AWS-hosted same app; then in-memory queue to RabbitMQ; then Celery only if framework effects are a research question). Keep `sdmodeler` code and CLI untouched.

Run focused tests during development and the full documented `pytest` suite after implementation. Manually verify the documented local startup, k6 command, and end-to-end outputs. Report that no lint/type-check command is configured rather than introducing an unrelated tool.

**Expected changes:** `README.md` or `experiments/queueing_demo/README.md`, source/tests described above, and no changes to `sdmodeler`.

## Completion report

After implementation is approved and completed, write `.zencoder/chats/3f025934-f50a-47b2-a15a-206c57c10e52/report.md` with changed files, Python and k6 versions, exact test and smoke-run commands/results, dropped-iteration count, telemetry schema verification, and the main limitations or unverified behavior.
