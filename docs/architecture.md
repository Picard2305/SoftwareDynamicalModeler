# Architecture

## Repository boundary

This repository contains the research orchestration, examples, tests, archived queueing records, and documentation. The `sdmodeler` directory is a separately versioned Git submodule that contains the system-identification package. The parent repository records a specific submodule commit; update the submodule deliberately when changing the identification implementation.

There are two queueing-related areas with different responsibilities:

- `experiments/queueing_demo/` is a standalone local HTTP queue application and generic k6 demonstration. It does not feed data directly into the identification package.
- `runs/` contains the paper's fixed-service-time queueing study scripts, workload, archived experiment runs, and analysis outputs.

Do not treat the generic demo workload and the paper's A/B/C arrival-profile study as the same experiment.

## Component map

| Location | Responsibility |
|---|---|
| `sdmodeler/config.py` | Pydantic configuration for ingestion, state/input roles, segmentation, feature libraries, differentiation, optimization, and validation. |
| `sdmodeler/data/ingestion.py` | Reads configured data sources and prepares aligned, uniformly sampled frames. |
| `sdmodeler/data/segmentation.py` | Represents deployment boundaries and divides prepared data into eligible segments. |
| `sdmodeler/identification/plant_controller.py` | Applies the configured state (`x`) and input (`u`) split. |
| `sdmodeler/identification/mode_switching.py` | Partitions a segment according to configured state thresholds. |
| `sdmodeler/identification/feature_library.py` | Builds the configured linear/domain, polynomial/interaction, or Fourier candidate terms. |
| `sdmodeler/identification/differentiation.py` | Estimates state derivatives from sampled observations. |
| `sdmodeler/identification/optimizer.py` | Performs sparse coefficient estimation using the selected optimizer. |
| `sdmodeler/identification/fitting.py` and `workflow.py` | Fit and coordinate candidates across configured segments, modes, and sparsity thresholds. |
| `sdmodeler/validation.py` | Integrates candidate models forward with supplied input trajectories and scores predicted trajectories. |
| `sdmodeler/reporting.py` | Builds reports, selects reportable candidates, compares models, and serializes results. |
| `sdmodeler/cli.py` and `sdmodeler/__main__.py` | Expose the package workflow through `python -m sdmodeler`. |
| `runs/analyze_queue_new_arrival_study.py` | Loads the archived queueing runs, fits/evaluates the study models, and writes aggregate results. |
| `runs/run_queue_service_time_profile.py` and `runs/queue_service_time_profile.js` | Collect a fixed-service-time profile run using the Python runner and k6 workload. |
| `tests/` | Tests the package-facing behavior, experiment analysis reproduction, and repository hygiene. |

The source layout in the submodule is authoritative. The Python import name remains `sdmodeler`; its files are organized into `data/` and `identification/` subpackages rather than all residing directly at the package root.

## Identification data flow

The package's high-level workflow is:

1. Read telemetry or a prebuilt canonical CSV and align/resample variables according to their configured roles.
2. Apply configured deployment boundaries and regime segmentation.
3. Separate state and input variables, then apply optional threshold-defined modes.
4. Build the configured feature matrix and check its conditioning.
5. Estimate state derivatives and fit sparse candidates across the configured threshold grid.
6. Integrate candidates forward using the supplied input trajectory and score their simulated states against observations.
7. Select reportable candidates, render reports, compare models, or export serialized output.

The state/input designation and segmentation rules come from configuration; they are not discovered automatically by the pipeline. See [the mathematical foundations](mathematical-foundations.md) for the model assumptions and interpretation limits.

## Queueing study data flow

The queueing-study runner and k6 workload create a run directory under `runs/<run_id>/`. That directory contains app telemetry, request and job records, workload metrics, configuration, and validation artifacts. The analysis script consumes the selected archived runs, reconstructs the one-worker FIFO waiting-queue trajectory, fits or evaluates the study's prediction methods, and writes a JSON result. The committed analysis result is the reference used by the reproduction test.

This research analysis is separate from `sdmodeler`'s general-purpose ingestion and fitting CLI. Its specialized queue reconstruction, model variants, and evaluation metrics are documented in [the queueing-study guide](../runs/README.md).

## Configuration and artifact ownership

- `examples/` contains synthetic configuration and sample data for exercising the general CLI.
- `requirements-analysis.txt` and `requirements-test.txt` define the pinned Python environments.
- `.python-version` selects the Python interpreter used by local setup and CI.
- `.github/workflows/reproduce-queue-analysis.yml` verifies the committed queue-analysis reproduction and repository hygiene without rerunning k6.
- The parent Git commit pins the `sdmodeler` submodule revision; the analysis output records source and runtime provenance when available.

Derived or generated outputs should be written to a separate destination when reproducing results. The analysis CLI's `--output` option is designed to avoid replacing the committed reference JSON accidentally.
