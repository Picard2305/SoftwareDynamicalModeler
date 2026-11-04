# SoftwareDynamicalModeler

SoftwareDynamicalModeler is a batch system-identification tool for learning interpretable sparse dynamical models from time-series telemetry. It separates state variables from exogenous inputs, fits candidate nonlinear models, validates them by forward simulation, and reports model equations and metadata.

The primary implementation is maintained in the `sdmodeler` Git submodule. This repository pins the submodule revision and contains examples, tests, and queueing research experiments. The local queueing demo under `experiments/queueing_demo` is a separate application; it is not an integration layer for `sdmodeler`.

## Contents

- [Installation and quick start](#installation-and-quick-start)
- [Run the tests](#run-the-tests)
- [Reproduce the queueing analysis](#reproduce-the-queueing-analysis)
- [How the identification pipeline works](#how-the-identification-pipeline-works)
- [Interpreting model output](#interpreting-model-output)
- [Documentation map](#documentation-map)
- [Known limitations](#known-limitations)

## Installation and quick start

Use Python 3.14.2, the interpreter version recorded in `.python-version`. The analysis and test dependency files pin the Python package versions used by the reproduction workflow.

Clone the repository with its pinned submodule:

```powershell
git clone --recurse-submodules https://github.com/Picard2305/SoftwareDynamicalModeler.git
cd SoftwareDynamicalModeler
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-test.txt
```

On macOS or Linux, create and activate the environment with `python3.14 -m venv .venv` and `source .venv/bin/activate` instead. If you already cloned without submodules, run `git submodule update --init --recursive` from the repository root.

To inspect the available CLI options after installation:

```powershell
python -m sdmodeler --help
python -m sdmodeler run --help
```

The example configuration and CSV under `examples/` are synthetic inputs, not a production AWS configuration. For live ingestion, real data, and the meaning of each configuration option, see [the mathematical foundations](docs/mathematical-foundations.md) and the source locations in [the architecture guide](docs/architecture.md).

## Run the tests

From the repository root:

```powershell
python -m pytest
```

The GitHub Actions workflow also runs the queue-analysis reproduction and repository-hygiene tests on pushes and pull requests. It checks out the pinned submodule and installs the test dependencies from `requirements-test.txt`.

## Reproduce the queueing analysis

The paper's queueing study is distinct from the generic local queue application. The archived raw run records are included in this repository, so reproducing the analysis does not require k6 or a new load test:

```powershell
python runs/analyze_queue_new_arrival_study.py --output runs/queue-new-arrival-profile-study-reproduced.json
```

The analysis uses the recorded training and held-out run IDs, writes to the requested output, and leaves the committed reference JSON unchanged. The reproduction test compares the regenerated aggregate metrics against that reference. Full experimental design, run selection, data validation, model definitions, and reported results are in [the queueing-study guide](runs/README.md). Repeating the load tests requires the external k6 executable; see that guide for the distinction between archived-data analysis and new data collection.

## How the identification pipeline works

For a state vector `x` and external input vector `u`, the tool fits a model of the form `dx/dt = f(x, u)`. It constructs candidate features from configured library tiers, estimates derivatives from sampled telemetry, and uses sparse regression to select a compact set of terms. Candidates are validated by integrating them forward from observed initial conditions and comparing their trajectories with the observed data.

The pipeline supports state/control separation, segmentation, optional threshold-based modes, condition-number warnings, several differentiation and sparse-regression methods, forward-simulation validation, reporting, and JSON export. The detailed formulation, assumptions, metrics, and interpretation cautions are in [the mathematical foundations](docs/mathematical-foundations.md).

## Interpreting model output

A fitted coefficient is conditional on the chosen variables, units, sampling interval, segment, mode, feature library, and correlated inputs. It is not by itself evidence of causation. Check the condition-number warning and validation status before using a model to form an operational hypothesis; confirm proposed interventions with an experiment or other causal design.

For example, in a model such as `dCPU/dt = -a*CPU + b*Instances`, a negative `CPU` coefficient is consistent with self-relaxation over the observed regime, and the `Instances` coefficient describes a fitted contribution to the instantaneous rate of change. These terms should not be interpreted as a universal capacity law outside the data range or experimental setup used to fit them.

The default report/export selection uses `reportable_candidates()`. It normally includes validation-reliable candidates and can also include specified unvalidated Tier 3 Fourier candidates. Pass `--include-unreliable` to include the broader set of candidates. See [the mathematical foundations](docs/mathematical-foundations.md#validation-and-reporting) for the precise distinction and limitations.

## Documentation map

- [Mathematical foundations](docs/mathematical-foundations.md): model formulation, fitting, validation, interpretation, and limitations.
- [Queueing-study guide](runs/README.md): paper experiment design, archived run IDs, data checks, analysis, and results.
- [Reproducibility guide](docs/reproducibility.md): clean checkout, Python environment, reproduction commands, and CI behavior.
- [Architecture guide](docs/architecture.md): repository layout, submodule boundary, and component responsibilities.
- [Queueing demo guide](experiments/queueing_demo/README.md): separate local HTTP queue application and generic k6 workload.
- `plan.md`: historical development plan, not a current setup or reproduction guide.

## Known limitations

- Validation is a forward-integration check on a trailing portion of the fitting frame, not a fully held-out train/validation split.
- The configured state/input split is an assumption. A nominal input that responds to a modeled state can introduce feedback not captured by the split.
- Regime and mode definitions are configured; automatic changepoint and threshold discovery are not provided by the current workflow.
- The tool fits batch telemetry for a single system and does not compose multi-service dependency graphs or perform live streaming identification.
- Sparse identification is sensitive to noise, sampling, scaling, and collinearity. A good fit does not establish a causal mechanism.
