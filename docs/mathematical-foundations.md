# Mathematical foundations

## Problem formulation

The identification workflow assumes that a system can be represented, over a selected time window, by a controlled dynamical system

\[
\dot{x}(t)=f(x(t),u(t)),
\]

where `x` contains state variables whose evolution is being modeled and `u` contains externally imposed or otherwise designated inputs. For sampled telemetry at times `t_k`, the data are arranged as state observations `X = [x(t_k)]` and input observations `U = [u(t_k)]`. The input/state split is supplied through configuration; it is a modeling assumption, not something inferred automatically from correlation.

The target is a compact approximation of `f` over the observed domain. The fitted equation is conditional on the variables, units, sampling, input split, segment, and candidate feature library used to obtain it. It should not be interpreted as a universal law or causal conclusion.

## Sparse feature representation

SINDy approximates the dynamics as a linear combination of nonlinear candidate functions:

\[
\dot{X} \approx \Theta(X,U)\Xi.
\]

`Theta` is a design matrix whose columns are candidate features evaluated on the observations. `Xi` contains coefficients, with many coefficients constrained or selected to be zero. The sparse-regression objective can be described conceptually as balancing derivative-fit error against model complexity; the configured optimizers implement practical approximations to that goal rather than solving a general exact minimum-term problem.

The code supports three library tiers:

- **Tier 1** contains linear state terms and configured domain terms. Inputs are represented as control terms in the model.
- **Tier 2** adds configured polynomial and pairwise interaction terms. It can represent nonlinear effects but increases the number of possible terms and the risk of collinearity and overfitting.
- **Tier 3** adds Fourier terms for long-period seasonal structure. It is fitted separately from Tiers 1 and 2 because it represents a different time-scale question.

Before fitting, the design matrix condition number is checked against a configured warning threshold. A warning indicates that features are nearly linearly dependent and that coefficient attribution may be unstable. It does not necessarily prevent fitting, and it should be considered when interpreting individual coefficients.

## Derivative estimation and sparse regression

Telemetry provides sampled values rather than exact derivatives. The pipeline estimates derivatives using the configured method: finite differences, Savitzky-Golay differentiation, spline differentiation, or total-variation regularized differentiation. These methods trade simplicity, smoothing, and assumptions about derivative structure differently. Noise and sampling cadence affect the resulting derivative estimates and therefore the fitted coefficients.

The supported sparse-regression methods include Sequentially Thresholded Least Squares (STLSQ), SR3, and Elastic Net. A configured sparsity-threshold grid creates multiple candidate fits. The workflow does not select a candidate solely from in-sample derivative fit: trajectory validation is a separate stage because derivative-fit quality alone does not establish that the integrated model behaves plausibly.

Coefficients inherit the units of the chosen variables and time coordinate. Changing units, sampling interval, or feature scaling changes their numerical values. Interpret coefficients only with that context.

## Segments and modes

A single global equation can be inappropriate when system behavior changes across deployments, operating regimes, or threshold-defined states. The workflow can split observations at configured deployment boundaries, apply configured regime labels, and partition segments into threshold-based modes. It then fits separate candidates for each eligible segment/mode window.

This construction represents piecewise dynamics. It does not discover all changepoints or thresholds automatically, and a short or nearly constant segment may not contain enough information for meaningful identification. Such segments can be marked as having insufficient length or dynamic range and skipped.

## Forward-simulation validation

For each fitted candidate, the validation workflow integrates the learned dynamics forward from observed initial conditions. The configured input trajectory is supplied as an exogenous signal and held according to the simulator's step convention. The predicted state is compared with the observed trajectory over the configured horizon.

Validation supports two metrics with opposite directions:

| Metric | Meaning | Direction for better fit |
|---|---|---|
| `NORMALIZED_RMSE` | Root-mean-square trajectory error normalized by the scale of the observed trajectory | Lower |
| `TRAJECTORY_DIVERGENCE_TIME` | Elapsed time before the prediction departs by more than the implementation's 20% relative-error threshold | Higher |

Reliability is decided using the configured metric and acceptance threshold. A score cannot be interpreted without knowing which metric was used. The current workflow validates on a trailing window of the same frame used for fitting; this is a forward-integration check, not an independent held-out test set.

### Validation and reporting

The reporting/export default is based on `reportable_candidates()`, not simply on a universal rule that every reported model has passed trajectory validation. The selection normally includes reliable models and can also include designated unvalidated Tier 3 Fourier candidates. `--include-unreliable` broadens the selected candidate set. Review each model's validation status and library tier in its report or serialized metadata.

A reliable validation result means only that the candidate met the configured trajectory criterion on the available validation window. It does not prove that the model will extrapolate to new operating conditions, that its coefficients are causal, or that a proposed intervention will have the predicted effect.

## Interpreting equations

Consider the illustrative scalar model

\[
\dot{x}=-a x + b u.
\]

If `a > 0`, the isolated linear state term is stable and has time constant `1/a` in the time units used by the data. The input coefficient describes its fitted contribution to the instantaneous rate of change while holding the other modeled variables fixed. If `a` is nonpositive, the isolated state term does not describe stable decay. In either case, the interpretation is local to the fitted regime and may be distorted by omitted variables, collinearity, noise, or an incorrect state/input split.

An interaction term such as `RequestRate * ErrorRate` allows the fitted rate of change to depend jointly on both quantities. Its presence is a useful hypothesis about structure to investigate. It does not by itself prove a retry mechanism or a feedback loop; an experiment that independently varies the relevant factors is needed to support that causal interpretation.

Likewise, differences between coefficients from two deployment models can flag a change in fitted dynamics. They should be compared only when data definitions, units, sampling, feature libraries, and operating regimes are sufficiently comparable.

## Assumptions and limitations

- The state/input distinction is configured. A variable designated as an input may still be endogenous or respond to modeled state variables.
- Sampling and resampling choices affect derivative estimates and fitted dynamics. State and input variables can use different configured resampling rules.
- Sparse models can be non-identifiable when candidate features are collinear or when data do not sufficiently excite the system.
- Model reliability is criterion- and window-specific. The present workflow does not provide a full held-out evaluation split by default.
- Threshold-based modes and deployment boundaries are configuration-driven; automatic changepoint and threshold discovery are not part of the current workflow.
- The workflow is batch-oriented and identifies single-system dynamics. It does not automatically compose service dependency graphs or establish causal effects.

## Implementation locations

The identification implementation is in the `sdmodeler` submodule. The main responsibilities are organized under `sdmodeler/identification/` for differentiation, feature construction, sparse optimization, fitting, and mode switching; `sdmodeler/data/` for data preparation and segmentation; and the submodule's configuration, validation, reporting, and CLI modules. See [the architecture guide](architecture.md) for the component map and repository boundary.
