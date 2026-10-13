# Prompt: PySINDy-Based System Identification for AWS-Hosted Software Systems

## Objective

Build a Python program that discovers interpretable, sparse governing equations for the runtime dynamics of a distributed software system deployed on AWS, using time-series telemetry (CloudWatch metrics, application-level metrics, or equivalent). The program must treat the autoscaling controller as an exogenous input rather than part of the identified plant, segment data by deploy/regime rather than pooling indiscriminately, support hybrid/switching dynamics, and validate discovered models by forward simulation against held-out trajectories — not by in-sample fit quality alone.

Do not write code yet. Produce a design document / implementation plan covering the sections below, with enough specificity that implementation could begin directly from it. Flag any section where you need a design decision from me before proceeding.

---

## 1. Data ingestion and schema

- Define a canonical internal schema for a multivariate time series: timestamp, a set of **state variables** (continuous, e.g. CPU%, memory%, p50/p99 latency, queue depth, request rate, error rate), a set of **control/exogenous variables** (Instances count, deploy_id/version, feature_flag_state, autoscaling_policy_id), and a **regime/mode label** (to be assigned by the segmentation step, not by the ingestion step).
- Specify how to pull from CloudWatch (GetMetricData or GetMetricStatistics) plus at least one non-AWS-native source (e.g. application logs/APM for error rate and business-level request rate), and how to align mismatched sampling intervals (resampling/interpolation strategy — specify method, e.g. zero-order hold for control variables, linear interpolation only for genuinely continuous state variables, never for Instances).
- Specify handling of missing data, clock skew across sources, and metric unit normalization.
- Explicitly exclude Instances, deploy events, and feature-flag changes from anything that will later be numerically differentiated — they are control inputs, not smooth state.

## 2. Segmentation strategy

- Design a preprocessing stage that splits the raw time series into fit-able windows along two axes:
  - **Deploy/version boundaries**: hard splits at known deploy timestamps (pulled from CI/CD or AWS CodeDeploy/ECS deployment events). Never fit a single model across a deploy boundary by default; make this an explicit opt-in override.
  - **Traffic/operating regime**: a documented method for identifying regime changes within a deploy window (e.g. business-hours vs off-hours via a simple time-of-day rule for a first pass; leave a hook for a change-point detection method — e.g. ruptures or Bayesian online changepoint detection — as a later enhancement, not required for v1).
- Each resulting segment should carry metadata: deploy_id, regime_label, start/end timestamp, sample count, and a data-quality flag (e.g. "insufficient dynamic range" if key state variables barely vary within the segment — SINDy will not identify anything meaningful from a flat trace).
- Specify a minimum segment length / minimum dynamic-range threshold below which a segment is skipped rather than fit, and how that's surfaced to the user.

## 3. Separating plant from controller (SINDYc structure)

- Specify explicitly which variables are `x` (state to be identified) and which are `u` (control input passed to SINDYc), and require this split to be config-driven, not hardcoded, since it will vary by system.
- Default recommendation: `x = (CPU, Memory, Latency, QueueDepth, RequestRate, ErrorRate)`, `u = (Instances, deploy_flag, feature_flag_vector)`.
- Document the assumption this encodes (autoscaling reacts to `x` but is not itself part of the identified dynamics) and note where this assumption breaks (e.g. if RequestRate is itself partly a function of Latency via client-side retry/backoff — that's a real feedback loop inside `x`, not one you get to externalize, and should be called out as a known limitation rather than silently modeled wrong).
- Include a configuration option to *hold Instances constant* and filter the dataset to autoscaling-frozen windows only, for use with controlled load-test data where autoscaling was deliberately disabled — this is the cleanest identification setting and the program should make it easy to flag and prioritize such windows if present in the metadata.

## 4. Hybrid / switching dynamics

- Specify a mode-detection step, independent of and prior to SINDy fitting, that partitions each segment further into discrete "modes" based on documented threshold conditions (e.g. CPU above/below the known autoscaling trigger threshold, error rate above/below an alerting threshold). This should be config-driven (user supplies the thresholds they know are operationally meaningful) rather than learned in v1.
- Require the fitting pipeline to fit a **separate SINDy model per mode per segment**, and store mode boundaries alongside each fitted model so downstream simulation can switch models when a trajectory crosses a mode boundary.
- Note this as the mechanism that replaces trying to get `max(0, CPU-70)`-style hinge terms in a single global feature library to reconstruct switching behavior implicitly.

## 5. Feature library

- Specify a tiered feature library, not one large combined library:
  - **Tier 1 (default, v1)**: linear terms in all state variables, plus a short hand-specified list of domain terms (queueing ratio, `1 - cache_hit_rate`, retry×error interaction) — explicitly not auto-generated, supplied by the user per-system since these are domain knowledge, not discoverable.
  - **Tier 2 (opt-in)**: low-order polynomial (degree ≤ 2) and pairwise interaction terms.
  - **Tier 3 (opt-in, separate fit)**: Fourier terms for diurnal/weekly structure, fit only on long-duration, single-deploy segments spanning multiple days — never combined in the same regression as Tier 1/2 fast-timescale terms, and documented as a different modeling question (long-run seasonal structure vs. short-run plant dynamics).
- Require a collinearity/condition-number check on the resulting design matrix before fitting, with a warning (not a silent failure) if condition number exceeds a configurable threshold — this is where CPU/RequestRate collinearity will show up.

## 6. Differentiation and optimizer configuration

- Make the differentiation method (finite difference, Savitzky-Golay, spline, total variation) a per-run config choice, defaulting to Savitzky-Golay with a documented default window, and require the tool to report the chosen method and parameters alongside every fitted model for reproducibility.
- Make the optimizer (STLSQ vs SR3 vs an elastic-net variant) a per-run config choice with STLSQ as default; document when to switch to SR3 (noisy/correlated telemetry — likely the common case here) as guidance text in the tool's output, not an automatic decision in v1.
- Require the sparsity threshold to be swept over a small grid (not a single fixed value) and require the tool to report a sparsity-vs-fit-error curve per fit so a human can sanity-check the threshold choice rather than trusting a default blindly.

## 7. Fitting workflow

- Define the end-to-end pipeline order explicitly: ingest → align/resample → segment (deploy + regime) → split mode → build feature library per tier → check conditioning → differentiate → fit per (segment, mode) → store model + metadata.
- Every fitted model artifact must store: the discovered coefficients, which library tier was used, the differentiation method/params, the optimizer/threshold used, the segment and mode it was fit on, sample count, and fit diagnostics (R² and a simulation-based validation score — see Section 8).

## 8. Validation

- Require forward-simulation validation as the primary acceptance criterion, not in-sample R²: for each fitted (segment, mode) model, integrate forward from a held-out initial condition drawn from the same segment/mode and compare against the actual trajectory over a configurable horizon (e.g. next 5–15 minutes of telemetry at native sampling rate).
- Define a concrete divergence metric (e.g. normalized RMSE over the simulation horizon, or trajectory divergence time — how long before simulated and actual trajectories differ by more than X%) and a configurable acceptance threshold below which a model is flagged "unreliable" and excluded from downstream use/reporting rather than silently kept.
- Require this validation to run automatically as part of the fitting workflow, not as a separate manual step.

## 9. Output / reporting

- Produce human-readable output per fitted model: the discovered equation in standard mathematical notation (not just raw coefficient arrays), the segment/mode it applies to, and its validation score.
- Produce a cross-deploy comparison view: given two fitted models for the same regime across two different deploy_ids, report which coefficients changed and by how much — this is the actual engineering payoff (did this deploy change system dynamics, and how) and should be a first-class output, not an afterthought.
- Support exporting the fitted model set in a serialized form (coefficients + metadata, e.g. JSON) suitable for later use in simulation/what-if tooling.

## 10. Explicit non-goals for v1

- No automatic changepoint detection (regime boundaries are user/config-supplied via known thresholds and deploy timestamps).
- No automatic mode-threshold discovery (thresholds are user-supplied, matching known operational trigger points).
- No multi-service / graph composition in this program — this tool identifies single-service, single-node dynamics only. Composition across services (e.g. via port-Hamiltonian interconnection) is out of scope here and should be designed as a separate downstream consumer of this tool's serialized model outputs, not built into this program.
- No live/streaming fitting — batch fitting over historical windows only for v1.

## 11. Open design questions to resolve before implementation

List these explicitly in the design doc rather than guessing:
- What is the actual telemetry source and access method available (CloudWatch API directly, or an intermediate store like Prometheus/Timestream)?
- What is the native sampling interval, and does it differ across metrics?
- Is there an existing source of deploy-boundary timestamps (CI/CD system, CodeDeploy API) to key segmentation off of?
- Are load-test/chaos-engineering traces with autoscaling deliberately disabled available, or does the tool need to work with production-only, autoscaler-active data as its primary source?
- What are the known operational thresholds (autoscaling trigger %, alerting thresholds) to seed the mode-switching config?
