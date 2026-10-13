# SoftwareDynamicalModeler

PySINDy-based system identification for AWS-hosted software systems. This
tool discovers interpretable, sparse governing equations for the runtime
dynamics of a distributed software system (CPU, memory, latency, queue
depth, request rate, error rate, ...) from time-series telemetry, treating
the autoscaling controller as an exogenous input rather than part of the
identified plant. See `.\sindy-aws-telemetry-spec.md` for the full design
spec and `.\plan.md` for the implementation plan this codebase follows.

## 1. The math

### 1.1 What SINDy actually does

The system is modeled as a controlled nonlinear dynamical system:

```
dx/dt = f(x, u)
```

where `x` is the vector of **state variables** (things that evolve
continuously and are governed by the system's own internal dynamics — e.g.
CPU%, memory%, latency, queue depth) and `u` is the vector of **control /
exogenous inputs** (things imposed on the system from outside — e.g.
`Instances`, `deploy_flag`, `feature_flag_vector`). This structure is
SINDYc (SINDy with control), not plain SINDy.

**Sparse Identification of Nonlinear Dynamics (SINDy)** assumes `f` is a
*sparse linear combination* of a library of candidate nonlinear functions
(features) of `x` and `u`:

```
dx_i/dt = Theta(x, u) @ xi_i
```

- `Theta(x, u)` is the **feature library**: a matrix whose columns are
  candidate terms evaluated at every timestep (e.g. `CPU`, `Memory`,
  `CPU^2`, `CPU*RequestRate`, `Instances`, ...). This codebase builds
  `Theta` in tiers — see Section 1.3 below.
- `xi_i` is a **sparse coefficient vector** — most entries are exactly zero.
  Sparsity is what makes the discovered model interpretable: instead of a
  black-box fit, you get an actual short equation like
  `dCPU/dt = -0.02*CPU + 0.15*Instances`.
- Solving for `xi_i` is a sparse regression problem: given the numerically
  estimated derivative `dx/dt` (see Section 1.2) and the feature matrix
  `Theta`, find the sparsest `xi` that satisfies
  `dx/dt ~= Theta @ xi` to within some tolerance.

### 1.2 Differentiation (`sdmodeler\differentiation.py`)

SINDy needs `dx/dt`, but telemetry only gives you `x(t)` at discrete,
noisy samples. Differentiating noisy discrete data by naive finite
differences amplifies noise badly, so the differentiation *method* is a
first-class, per-run config choice (`DifferentiationConfig`):

- **finite_difference** — simplest, most noise-sensitive.
- **savitzky_golay** (default) — fits a local polynomial in a sliding
  window and differentiates that polynomial analytically; smooths noise
  while preserving derivative shape better than finite differences.
- **spline** — fits a smoothing spline through the data, differentiates
  the spline.
- **total_variation** (trend-filtered) — minimizes a total-variation
  penalty on the derivative; good when the true derivative is believed to
  be piecewise-constant/sparse in its own right.

Whichever method is used, its name and parameters (e.g. Savitzky-Golay
window length/polynomial order) are stored on every `FittedModel` so a
fitted equation is reproducible.

### 1.3 The feature library (`sdmodeler\feature_library.py`)

Rather than one large combined library (which invites overfitting and
makes the resulting equation harder to trust), the library is built in
**tiers**, matching spec Section 5:

- **Tier 1 (default)**: linear terms in every state variable, plus a short
  list of user-supplied domain terms (e.g. a queueing ratio, `1 -
  cache_hit_rate`) — these are domain knowledge, not auto-generated.
- **Tier 2 (opt-in)**: polynomial terms up to a configurable degree, plus
  pairwise interaction terms (e.g. `CPU^2`, `CPU*RequestRate`).
- **Tier 3 (opt-in, fit separately, never combined with 1/2)**: Fourier
  (sin/cos) terms at daily/weekly periods, for long-duration segments only.
  This captures diurnal/weekly seasonal structure — a fundamentally
  different modeling question (slow seasonal drift) from fast-timescale
  plant dynamics, so it is never regressed in the same fit as Tier 1/2.

Before fitting, the design matrix's **condition number**
(`np.linalg.cond(Theta)`) is checked against a configurable threshold.
A high condition number means some library columns are nearly linearly
dependent (e.g. CPU and RequestRate track each other closely) — the
regression can still run, but the resulting coefficients become unstable
and hard to trust. This is surfaced as a warning attached to the fitted
model, not a silent failure.

### 1.4 The optimizer / sparse regression (`sdmodeler\optimizer.py`)

Three optimizers are supported (`OptimizerConfig.method`):

- **STLSQ (Sequentially Thresholded Least Squares, default)**: alternate
  ordinary least squares with hard-thresholding small coefficients to
  zero, until the sparsity pattern stabilizes. Simple, fast, and the
  standard SINDy default, but sensitive to noise/collinearity.
- **SR3 (Sparse Relaxed Regularized Regression)**: a proximal/relaxation
  method that is generally more robust to noisy, correlated telemetry
  (the likely common case for AWS CloudWatch data) at some cost in
  interpretability of the exact thresholding mechanics.
- **Elastic net** (via scikit-learn `ElasticNet`, wrapped for PySINDy):
  combines L1 (sparsity) and L2 (shrinkage) penalties; a reasonable
  alternative when SR3 is unavailable or a more familiar regularization
  path is preferred.

Rather than trusting one fixed sparsity threshold, the threshold is
**swept over a small grid** (`OptimizerConfig.sparsity_threshold_grid`).
Every grid point produces a full candidate model with its own sparsity
pattern, coefficient values, and in-sample fit RMSE — see Section 1.6 for
why threshold *selection* is deliberately deferred to validation rather
than automated at fit time.

### 1.5 Segmentation and hybrid/switching dynamics

Real telemetry is not one long stationary trajectory suitable for a single
global fit:

- **Deploy boundaries** (`sdmodeler\segmentation.py`) are hard split
  points — a new deploy can change the system's actual dynamics, so
  fitting across a deploy boundary silently would blend two different
  physical systems into one (wrong) equation. This is opt-in only.
- **Regime labels** (business-hours vs. off-hours, in v1 via a simple
  time-of-day rule) further split each deploy window, since traffic
  regime can also change effective dynamics.
- **Modes** (`sdmodeler\mode_switching.py`) split a segment further still,
  based on user-supplied threshold conditions on state variables (e.g.
  "CPU > 70%", matching a known autoscaling trigger). This is how the
  tool captures genuinely **hybrid/switching dynamics** — e.g. the system
  might behave close to linearly below the autoscaling trigger and
  qualitatively differently above it — without needing brittle
  hinge/`max(0, ...)` terms inside a single feature library. Instead, a
  **separate SINDy model is fit per (segment, mode)**, and mode boundaries
  are stored so a downstream simulator can switch models when a
  trajectory crosses a boundary.

Each segment carries a data-quality flag (`insufficient_length` or
`insufficient_dynamic_range`) — SINDy cannot discover anything meaningful
from a window that's too short or too flat, so such segments are skipped
rather than silently (and meaninglessly) fit.

### 1.6 Validation — the actual acceptance criterion

**In-sample R² is not the acceptance criterion.** A sparse regression can
fit the *derivative* well in-sample while still producing an ODE that
diverges wildly once integrated forward — R² on `dx/dt` says nothing about
long-horizon trajectory behavior. Per spec Section 8, the primary
acceptance criterion is **forward-simulation validation**
(`sdmodeler\validation.py`):

1. Take the discovered ODE `dx/dt = Theta(x, u) @ xi` for a given
   (segment, mode, sparsity-threshold) candidate.
2. Integrate it forward numerically via 4th-order Runge-Kutta (RK4),
   starting from a real initial condition, holding `u` at its actual
   (zero-order-hold) trajectory at each step — since `u` is an exogenous
   input, not something the model predicts.
3. Compare the simulated trajectory to the actual trajectory over a
   configurable horizon (`ValidationConfig.horizon_minutes`).
4. Score the divergence with one of two metrics:
   - **normalized RMSE**: RMS error between simulated and actual,
     normalized by the actual trajectory's own scale.
   - **trajectory divergence time**: how long (in seconds) before the
     simulated and actual trajectories differ by more than a fixed
     relative threshold (20%).
5. A model is flagged `is_reliable` based on whether its score passes a
   configurable acceptance threshold. **Unreliable models are excluded
   from downstream reporting/export by default**, not silently kept.

**The two metrics have opposite directions — don't read "Validation
score" as a generic quality score without knowing which metric produced
it:**

| Metric | What the number means | Direction | Reliable when |
|---|---|---|---|
| `NORMALIZED_RMSE` (default) | error magnitude: RMS deviation between simulated and actual trajectory, normalized by the actual trajectory's own standard deviation | **lower is better** | `score <= acceptance_threshold` |
| `TRAJECTORY_DIVERGENCE_TIME` | seconds elapsed before simulated and actual trajectories diverge by more than 20% | **higher is better** | `score >= acceptance_threshold` |

So `Validation score: 0.031 (reliable=True)` under the default
`NORMALIZED_RMSE` metric means the simulated trajectory deviated from
actual by about 3.1% of the actual trajectory's own scale over the
validation horizon — a small error, well under the default
`acceptance_threshold=0.2`. Under `TRAJECTORY_DIVERGENCE_TIME`, a report
showing `Validation score: 600.0 (reliable=True)` instead means the
trajectories didn't diverge by more than 20% until 600 seconds into the
horizon — a long survival time is what makes it reliable there. Always
check `ModelReport`/config for which `divergence_metric` was configured
before comparing scores across runs or systems.

This is also *why* the sparsity threshold is swept rather than
auto-selected at fit time (Section 1.4): the "right" threshold is a
question of which candidate actually reproduces trajectory behavior, which
can only be answered after forward-simulation validation — not by
in-sample fit quality, which would be circular and indefensible as a
selection criterion.

**v1 limitation, explicitly flagged (not hidden):** the current fitting
workflow fits each candidate on the entire segment/mode window, and
validation checks that same model against a *trailing window of the same
frame* it was fit on. This is a meaningful check (integrating an ODE
forward is a fundamentally different test than in-sample derivative fit),
but it is not a fully held-out train/validation split. A true held-out
split is a natural v2 enhancement.

## 2. Pipeline order

```
ingest (CloudWatch / CSV)
  -> align/resample (zero-order hold for control vars, linear interp for state vars)
  -> segment (deploy boundary + regime label)
  -> split mode (per-segment threshold-based partition)
  -> build feature library (per configured tier)
  -> check conditioning (design matrix condition number)
  -> differentiate (configured method)
  -> fit per (segment, mode) x per sparsity threshold  [sdmodeler\fitting.py]
  -> validate by forward simulation                     [sdmodeler\validation.py]
  -> report / compare / export                          [sdmodeler\reporting.py]
```

## 3. Usage

### 3.0 CLI (recommended entry point)

The primary way to run the pipeline is the `sdmodeler` CLI:

```
python -m sdmodeler run \
  --config config.json \
  --start 2024-01-01T00:00:00Z --end 2024-01-02T00:00:00Z \
  --target-interval-seconds 60 \
  --deploy-boundaries deploys.json \
  --output models.json
```

- `--config` is a JSON (or YAML, if PyYAML is installed) file that
  deserializes into a `PipelineConfig` (see Section 3.1) — you can produce
  one by building a `PipelineConfig` in Python once and dumping it with
  `json.dump(config.model_dump(mode="json"), f)`.
- `--start`/`--end`/`--target-interval-seconds` define the live-ingestion
  window (fetched via each variable's configured CloudWatch/non-AWS
  source). If you already have a canonical, uniformly-sampled frame (e.g.
  from a prior export or test fixture), skip live ingestion entirely with
  `--input-csv telemetry.csv` (must have a `timestamp` column plus one
  column per configured variable) instead of `--start`/`--end`/
  `--target-interval-seconds`.
- `--deploy-boundaries` is optional: a JSON file `[{"deploy_id": "v42",
  "start": "2024-01-01T12:00:00Z"}, ...]`.
- By default, only validation-reliable models (spec Section 8) are
  printed and exported; pass `--include-unreliable` to see everything,
  including candidates that failed forward-simulation validation.
- The run refuses to proceed against unresolved Section 11 open design
  questions (see Section 3.1) unless you pass
  `--allow-unresolved-design-questions` — useful for dry runs against
  synthetic/test data before your environment's real answers are known.
- `--output models.json` writes the serialized model set (Section 3.3);
  omit it to just print reports to stdout.

For anything beyond the CLI's flags (custom deploy-boundary sources,
programmatic access to `FitResult`/`ValidationResult` objects, etc.), drive
the pipeline directly from Python as shown in Sections 3.1-3.3.

### 3.1 Configuration

Everything config-driven lives in `sdmodeler\config.py` as Pydantic models,
composed into a single `PipelineConfig`. Several fields are placeholders
for open design questions (spec Section 11) that must be filled in with
real values from your environment before a production run:

```python
from sdmodeler.config import PipelineConfig, OpenDesignQuestions

config = PipelineConfig(
    # ... ingestion, segmentation, plant_controller_split, mode_switching,
    #     feature_library, differentiation, optimizer, validation ...
    open_design_questions=OpenDesignQuestions(
        telemetry_access_method_confirmed="cloudwatch_get_metric_data",
        native_sampling_interval_confirmed=True,
        deploy_boundary_source_confirmed=True,
        autoscaler_disabled_loadtest_data_available=False,
        known_operational_thresholds_seeded=True,
    ),
)

config.assert_ready_for_production_run()  # raises if any open question is still unresolved
```

### 3.2 Running the fitting workflow

```python
from sdmodeler.fitting import run_fitting_workflow
from sdmodeler.segmentation import DeployBoundary

fit_results = run_fitting_workflow(
    df,                      # canonical DataFrame, e.g. from build_canonical_frame(...)
    config,
    deploy_boundaries=[DeployBoundary(deploy_id="v42", start=pd.Timestamp(...))],
)
```

`fit_results` is a `list[FitResult]`, one per (segment, mode), each holding
a `candidates: list[FittedModel]` — one candidate per sparsity-threshold
grid point, already validated (`candidate.validation_score` and
`candidate.is_reliable` are populated).

### 3.3 Interpreting output

```python
from sdmodeler.reporting import build_model_report, reliable_candidates, compare_models, export_fit_results

# Human-readable equation + metadata for one model
for candidate in fit_results[0].candidates:
    print(build_model_report(candidate).render())

# Only validation-reliable models, across the whole run
models = reliable_candidates(fit_results)

# Cross-deploy comparison: same regime, two different deploy_ids
comparison = compare_models(model_from_deploy_v1, model_from_deploy_v2)
print(comparison.render())

# Serialize reliable models (default) to JSON for downstream simulation/what-if tooling
export_fit_results(fit_results, "models.json")
```

`build_model_report(...).render()` produces output like:

```
Segment: deploy_id=v42 regime=business_hours mode=default [2024-01-01 00:00:00+00:00 -> 2024-01-01 02:00:00+00:00] (n=120)
Library tiers: tier_1_linear_domain
Differentiation: savitzky_golay; Optimizer: stlsq (threshold=0.05, 2 nonzero terms)
In-sample R^2: 0.9421
Validation score: 0.031 (reliable=True)
Discovered dynamics:
  dCPU/dt = -0.0421*CPU + 0.1830*u:Instances
```

`compare_models(...).render()` produces output like:

```
Comparing deploy 'v41' vs 'v42' (regime=business_hours, mode=default)
  dCPU/dt : CPU: -0.0300 -> -0.0421 (delta=-0.0121)
  dCPU/dt : u:Instances: 0.1500 -> 0.1830 (delta=+0.0330)
```

which is the actual engineering payoff described in spec Section 9: did a
given deploy change the system's identified dynamics, and by how much.

## 4. Interpreting discovered dynamics: worked examples

A discovered equation is only useful if you can read off what it implies
operationally. This section walks through representative equations this
tool might produce and what each term means for the engineer looking at
it. All examples assume `report = build_model_report(candidate)` and
`report.render()` prints the equation, as in Section 3.3.

### 4.1 A single self-regulating state: response time constant

```
dCPU/dt = -0.0420*CPU + 0.1830*u:Instances
```

**How to read it:** this is a linear first-order ODE in `CPU` driven by
`Instances`. Two numbers matter:

- **The self-coefficient (`-0.0420` on `CPU`) is the system's own decay
  rate.** Its reciprocal, `1/0.0420 ≈ 23.8` (in whatever time unit `dt`
  was expressed in — seconds, if `t_seconds` was used, as it is by
  default), is the **time constant**: roughly how long the system takes to
  settle after a disturbance (e.g. after a traffic spike ends, CPU decays
  back toward baseline on this timescale). A negative coefficient here is
  what makes the system stable/self-correcting; if this coefficient came
  out **positive**, the model says CPU would grow without bound in the
  absence of Instances changes — i.e. either the system is genuinely
  unstable at this load level, or (more likely for a real system) the
  segment/mode window was too short or too noisy to identify true
  self-regulating behavior. Check `condition_number_warning` and
  `is_reliable` before trusting a positive self-coefficient.
- **The control coefficient (`0.1830` on `u:Instances`) is the marginal
  effect of one more instance.** Concretely: for every additional
  instance, `dCPU/dt` increases by 0.183 (percentage points per second,
  if CPU is in % and time is in seconds) *holding CPU itself fixed* — this
  is the instantaneous rate contribution, not a steady-state CPU change.
  Combined with the decay term, the **steady-state CPU per instance
  count** is `0.1830 / 0.0420 ≈ 4.36` (percentage points of CPU per
  instance, at equilibrium). This is directly actionable: it's an
  empirically-derived scaling ratio you can compare against your
  autoscaling policy's assumed CPU-per-instance target. If the autoscaler
  is configured assuming a different ratio, that's a concrete,
  measured mismatch to fix.

**Engineering implication:** the time constant tells you whether your
autoscaling cooldown period is well-matched to how fast the system
actually responds — a cooldown much shorter than `1/0.0420` risks
oscillatory scaling (scale up, see CPU still high because it hasn't
settled yet, scale up again). The steady-state ratio tells you whether
your scaling policy's assumed capacity-per-instance is accurate.

### 4.2 A coupling term: queueing behavior

```
dLatency/dt = -0.0150*Latency + 0.0080*QueueDepth
```

**How to read it:** `Latency` isn't just decaying on its own — it's being
driven by `QueueDepth`. A positive coefficient on `QueueDepth` says
queueing pressure directly translates into rising latency, at a rate of
0.008 latency-units/second per unit of queue depth.

**Engineering implication:** this is empirical confirmation (not an
assumption) that latency in this segment/mode is queueing-bound rather
than, say, CPU-bound or dominated by an external dependency. That
justifies capacity or concurrency-limit changes aimed specifically at
queue depth (e.g. thread pool sizing, connection pool limits) rather than
raw CPU scaling — and it gives you a quantitative lever: reducing
steady-state queue depth by `N` should reduce latency's driving term by
`0.008 * N`.

### 4.3 A Tier 2 interaction term: retry amplification risk

```
dErrorRate/dt = -0.0200*ErrorRate + 0.0035*(RequestRate*ErrorRate)
```

**How to read it:** the `RequestRate*ErrorRate` interaction term (only
available with `FeatureLibraryTier.TIER_2_POLYNOMIAL` enabled) means the
error rate's own growth rate scales with the *product* of request rate
and the current error rate — not with either alone. This is the
signature of a **positive feedback loop**: errors trigger client-side
retries, retries add to request rate, higher request rate at a given
error probability produces more absolute errors, which drives more
retries.

**Engineering implication:** a nonzero coefficient here is a discovered,
quantitative early-warning sign of retry-storm/cascading-failure risk
specifically at high request-rate + high error-rate combinations, which a
purely linear model (Section 4.1's form) cannot represent at all — this
is exactly why Tier 2 exists. If this term is present and its coefficient
is large enough that `-0.0200 + 0.0035*RequestRate > 0` for realistic
`RequestRate` values, the model predicts a self-sustaining error-rate
blowup once request rate crosses `0.0200/0.0035 ≈ 5.7` — a concrete,
testable threshold for where to add retry budgets/circuit breakers.

### 4.4 Mode-switching output: behavior change at a known threshold

Given `ModeSwitchingConfig` thresholds on `CPU`, the workflow fits a
separate model per mode. Suppose the reports show:

```
Segment: ... mode=CPU_gt_70.0=False ...
Discovered dynamics:
  dLatency/dt = -0.0500*Latency + 0.0020*QueueDepth

Segment: ... mode=CPU_gt_70.0=True ...
Discovered dynamics:
  dLatency/dt = -0.0180*Latency + 0.0210*QueueDepth
```

**How to read it:** below the 70% CPU threshold, latency responds weakly
to queue depth (`0.0020`) and settles quickly (large decay magnitude,
short time constant). Above the threshold, the queue-depth coupling is
**10x larger** (`0.0210`) and the system settles far more slowly (smaller
decay magnitude, long time constant) — the system is qualitatively
different once CPU crosses the autoscaling trigger, not just quantitatively
scaled.

**Engineering implication:** this is direct evidence that the current
autoscaling trigger (70%) is placed at a threshold where dynamics
genuinely change — supporting the trigger's placement — or, if the
"above-threshold" dynamics look bad enough (very slow settling, strong
queue coupling), evidence that the trigger should fire *earlier* (a lower
CPU%) to avoid ever operating in the degraded regime in the first place.

### 4.5 Cross-deploy comparison: did a deploy regress system dynamics?

```python
comparison = compare_models(model_from_deploy_v41, model_from_deploy_v42)
print(comparison.render())
```

```
Comparing deploy 'v41' vs 'v42' (regime=business_hours, mode=default)
  dCPU/dt : CPU: -0.0300 -> -0.0421 (delta=-0.0121)
  dCPU/dt : u:Instances: 0.1500 -> 0.1830 (delta=+0.0330)
```

**How to read it:** post-deploy (`v42`), the self-decay coefficient got
*more negative* (faster settling, shorter time constant — good) but the
per-instance effect went *up* (`0.1830` vs `0.1500` — the new version
needs more CPU per instance to do the same work, i.e. it's less
CPU-efficient — worth investigating, possibly not good).

**Engineering implication:** this is the tool's core payoff per spec
Section 9 — a quantitative, per-coefficient diff of *how* a deploy changed
system dynamics, not just whether some dashboard metric moved. A
consistent pattern of the per-instance coefficient increasing across
several deploys is a measurable efficiency regression trend worth flagging
in review, even if no single deploy's change looks alarming on its own.

### 4.6 When *not* to act on an equation

Two output fields exist specifically to stop you from over-trusting a
discovered equation:

- **`condition_number_warning`** (non-`None`): some feature-library
  columns are nearly collinear (classically, `CPU` and `RequestRate`
  tracking each other closely under normal load). The regression can
  still produce coefficients, but it cannot reliably attribute the effect
  to one variable vs. the other — e.g. it might assign the causal effect
  to `RequestRate` when it's really `CPU`, or split it arbitrarily between
  both. **Engineering implication:** don't make a decision that depends on
  *which* of two collinear variables is "responsible" without a
  decorrelating experiment (e.g. a load test that varies request rate at
  fixed instance count, or vice versa).
- **`is_reliable=False`** (from forward-simulation validation, Section
  1.6): the equation may fit the in-sample derivative reasonably but
  diverges from reality once integrated forward. **Engineering
  implication:** never use an unreliable model's coefficients for
  capacity planning, alerting thresholds, or "what-if" simulation — this
  is exactly why `reliable_candidates(...)` and `export_fit_results(...,
  reliable_only=True)` (the default) filter these out automatically.

## 5. Known limitations (explicit, not hidden)

- Changepoint-based regime detection and automatic mode-threshold
  discovery are documented hooks, not implemented in v1 (regimes/modes
  are user/config-supplied).
- Validation reuses a trailing window of the fitting frame rather than a
  true held-out split (Section 1.6 above).
- The x/u split encodes an assumption — autoscaling reacts to `x` but is
  not itself part of the identified dynamics — that can break down if a
  control-labeled variable is actually partly endogenous (e.g.
  `RequestRate` responding to `Latency` via client-side retry/backoff).
  This is a real feedback loop inside `x`, not something this split
  externalizes correctly, and is called out in
  `PlantControllerSplitConfig.known_feedback_limitations`.
- No multi-service/graph composition; this tool identifies single-service
  dynamics only. No live/streaming fitting — batch only.

## 6. Running tests

```
pytest
```

(see `.\pyproject.toml` for pytest configuration).
