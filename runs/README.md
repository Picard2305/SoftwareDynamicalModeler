# Queueing research study

This guide covers the fixed-service-time queueing study whose results are stored in `queue-new-arrival-profile-study-002.json`. It is distinct from the generic four-stage local workload documented in `experiments/queueing_demo/README.md`.

The committed run records are the analysis inputs. Recreating the published analysis from those files does not require running the app or k6. The environment and reproduction procedure are in [the reproducibility guide](../docs/reproducibility.md).

## Experimental design

The experiment uses one FIFO worker and a configured service workload of 55 ms per job (`time.sleep`). App telemetry is sampled every second. The state evaluated by the analyzer is **waiting queue depth**, reconstructed from accepted and completed job timestamps under the one-worker FIFO assumption; it is not the total unfinished backlog.

Three training runs use the same baseline arrival schedule: 10 jobs/s for 30 seconds, 20 jobs/s for 30 seconds, then 10 jobs/s for 30 seconds. Three runs are used to estimate the sparse queue model and the service capacity used by the training-estimated reflected model.

The held-out tests use three new 90-second arrival profiles. The configured phases are:

| Profile | Time interval (s) | Phase | Arrival rate (jobs/s) |
|---|---:|---|---:|
| A | 0–30 | Baseline | 8 |
| A | 30–60 | Overload | 22 |
| A | 60–90 | Recovery | 8 |
| B | 0–20 | Baseline | 10 |
| B | 20–40 | Lower ramp | 21 |
| B | 40–60 | Higher ramp | 23 |
| B | 60–90 | Recovery | 10 |
| C | 0–15 | Baseline | 12 |
| C | 15–30 | Lower overload | 20 |
| C | 30–45 | Higher overload | 23 |
| C | 45–60 | Partial recovery | 16 |
| C | 60–90 | Recovery | 8 |

The k6 schedule starts are fractional and the configured phase durations are 14.99, 19.99, or 29.99 seconds, with phase boundaries represented at 15, 20, or 30-second offsets. The table describes the intended schedule, not an assertion that each HTTP request lands exactly on a wall-clock boundary.

### Run selection

| Role | Run IDs |
|---|---|
| Training | `queue-demo-service-time-55ms-profile-002`, `queue-demo-service-time-55ms-profile-003`, `queue-demo-service-time-55ms-profile-004` |
| Profile A held out | `queue-demo-new-profile-a-rep-002`, `queue-demo-new-profile-a-rep-003`, `queue-demo-new-profile-a-retry-001` |
| Profile B held out | `queue-demo-new-profile-b-rep-001`, `queue-demo-new-profile-b-rep-002`, `queue-demo-new-profile-b-retry-001` |
| Profile C held out | `queue-demo-new-profile-c-rep-001`, `queue-demo-new-profile-c-retry-001`, `queue-demo-new-profile-c-rep-003` |

The analyzer fixes these IDs explicitly. Replicate names not listed above are not included in the reported analysis. The training schedule and held-out schedule are kept separate; held-out queue and completion observations are used for scoring, not as forecast inputs.

## Data layout and validity checks

Each selected run is archived under `runs/<run_id>/`. The analysis reads the experiment configuration, app metadata and job records, k6 summary, and listener-probe results. The app and k6 outputs should be retained with the run because they provide both the reconstructed queue data and evidence about whether the scheduled workload was delivered.

A run is rejected unless all the following checks pass:

- The app used exactly one worker and shut down cleanly.
- Accepted and completed job totals both equal the experiment's expected request count; unfinished backlog and worker errors are zero.
- k6 started and completed the expected number of request attempts, dropped no iterations, recorded the expected accepted count, and recorded no non-accepted response or transport error.
- Every listener probe reports a successful connection.
- The number of jobs with both accepted and completed timestamps agrees with the expected request count.

These criteria establish basic run completeness and workload delivery; they do not remove host-level timing variation or prove that the test environment represents a production service.

To collect an additional standalone profile run, use a new run ID and one of the supported profiles:

```powershell
python runs/run_queue_service_time_profile.py queue-demo-new-profile-a-rep-004 --profile a
```

The runner requires k6 and writes a new run directory. A newly collected run is not automatically added to the fixed training or held-out sets; update the analyzer's selection and its regression expectations deliberately before claiming a new result.

## Models and forecast inputs

All forecasts start from an empty queue and use the known arrival schedule. They do not consume held-out queue or completion observations as forecast inputs. The reconstructed held-out queue is used to calculate scores.

The training data fit a sparse linear model with `QueueDepth`, a constant term, and `ArrivalRate` as features. The committed fit uses STLSQ with sparsity threshold 0.001 and Savitzky-Golay differentiation (window 7, polynomial order 2). Its recorded equation is approximately

\[
\frac{dQ}{dt}=-0.062918Q-4.638351+0.416358\lambda,
\]

where `Q` is waiting jobs and `lambda` is the arrival rate in jobs per second. This is the empirical fit over the training data, not a queueing identity.

The analyzer evaluates these prediction methods:

- **Sparse unconstrained**: forward simulation of the fitted sparse model; values are not constrained to be nonnegative.
- **Sparse nonnegative projection**: Runge-Kutta integration of the same fitted model with queue state projected onto nonnegative values.
- **Reflected, training-estimated capacity**: discrete queue balance using the inverse of the mean FIFO-inferred service time from training runs. The recorded training mean is approximately 56.384 ms, corresponding to an estimated capacity of 17.735 jobs/s.
- **Reflected, configured capacity reference**: the same discrete queue balance using the nominal 55 ms service workload, or 18.182 jobs/s.
- **Empty-queue baseline**: predicts zero waiting jobs throughout.

For a reflected model with a one-second time step, the update uses the arrival rate applying to the interval just traversed:

\[
Q_k=\max\left(0, Q_{k-1}+(\lambda_{k-1}-\mu)\Delta t\right),\qquad \Delta t=1\text{ s}.
\]

The sparse forward simulator is aligned to that same interval convention. This left-endpoint alignment matters when the arrival schedule changes at a phase boundary.

## Metrics and reported results

The analyzer scores forecasts after the initial sample using mean absolute error (MAE), root-mean-square error (RMSE), normalized RMSE, absolute peak-height error, and absolute peak-time error. It also records recovery-to-empty timing and negative predictions per run. Profile aggregates are the mean, minimum, and maximum across the three selected held-out runs.

The following table reports the committed per-profile mean MAE in jobs and mean absolute peak-time error in seconds. `Reflected (training capacity)` is the model using the service capacity estimated from training runs.

| Profile | Sparse unconstrained MAE | Sparse projected MAE | Reflected (training capacity) MAE | Reflected (55 ms reference) MAE | Peak-time error for each dynamic method |
|---|---:|---:|---:|---:|---:|
| A | 19.82 | 12.76 | 1.01 | 3.54 | 0 s |
| B | 27.96 | 26.58 | 3.43 | 7.87 | 0 s |
| C | 14.69 | 13.81 | 2.76 | 4.17 | 0 s |

These are descriptive means over three runs per profile, not confirmatory statistical results. The training-estimated reflected model has the lowest mean MAE among these methods for each profile in this archived analysis. The sparse model's performance is weaker in these cases, and its unconstrained form can predict negative queue values.

All four dynamic methods have zero **sample-grid** peak-time error in the committed analysis. The observed queue is sampled at one-second intervals, and peak time is computed on that discrete grid; this is not sub-second timing accuracy or evidence of a general forecasting guarantee. The empty-queue baseline is excluded from that statement because it has no positive predicted peak.

## Reproduce the JSON result

From the repository root, regenerate the JSON without overwriting the committed reference:

```powershell
python runs/analyze_queue_new_arrival_study.py --output runs/queue-new-arrival-profile-study-reproduced.json
```

The reference result is `runs/queue-new-arrival-profile-study-002.json`. The analyzer output includes the run IDs, model coefficients, service-capacity estimate, per-run scores, per-profile aggregates, and available source/runtime provenance. The reproduction test compares aggregate results with a numerical tolerance. See [the reproducibility guide](../docs/reproducibility.md) for setup and CI details.

## Interpretation limits

- Only three held-out runs are included per profile. Their aggregates are exploratory and are not a significance test.
- The waiting-queue reconstruction relies on complete accepted/completed timestamps and the one-worker FIFO service assumption.
- A 55 ms configured service delay does not ensure exactly 55 ms of observed wall-clock service time; the training estimate uses inferred service durations from archived runs.
- The reflected model is a simple queue-balance baseline. The sparse model is empirical and depends on its training range, derivative method, and selected features.
- One-second sampling limits timing resolution. A lower trajectory error for one method in these runs is not proof that it will outperform other approaches under different loads, servers, or service-time distributions.
