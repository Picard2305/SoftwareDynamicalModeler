# Reproducing the analysis

This guide describes how to recreate the Python environment and rerun the queueing-study analysis from the archived run records committed in this repository. It separates **analysis reproduction** from **collecting new load-test data**: only the latter requires k6 and a live queueing application.

## 1. Clone the repository and pinned submodule

The `sdmodeler` directory is a Git submodule. Clone recursively so the parent repository's recorded submodule revision is checked out:

```powershell
git clone --recurse-submodules https://github.com/Picard2305/SoftwareDynamicalModeler.git
cd SoftwareDynamicalModeler
```

For a repository already cloned without submodules:

```powershell
git submodule update --init --recursive
```

The submodule commit is pinned by the parent repository. Record both the parent and submodule revisions when archiving a new study result.

## 2. Create the Python environment

The repository selects Python 3.14.2 in `.python-version`. Create an isolated environment and install the pinned test requirements:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-test.txt
```

`requirements-test.txt` includes `requirements-analysis.txt` and adds the pinned test dependencies. To install only the packages used by the analysis, use `python -m pip install -r requirements-analysis.txt` instead. The committed requirement files pin package versions; the reproduction workflow uses the test requirements.

On macOS or Linux, substitute `python3.14 -m venv .venv` and `source .venv/bin/activate` for the Windows environment commands.

## 3. Run the tests

```powershell
python -m pytest
```

The full test suite exercises the project. The queue analysis reproduction test can also be run directly:

```powershell
python -m pytest tests/test_queue_analysis_reproducibility.py tests/test_repository_hygiene.py
```

## 4. Regenerate the queue-study analysis

From the repository root, write the reproduced result to a separate file:

```powershell
python runs/analyze_queue_new_arrival_study.py --output runs/queue-new-arrival-profile-study-reproduced.json
```

The analyzer derives repository paths from its own location. A relative `--output` path is resolved from the repository root; parent directories are created as needed. Omitting `--output` prints the result without replacing the committed reference file.

The reproduction test evaluates the regenerated aggregate metrics against `runs/queue-new-arrival-profile-study-002.json` and checks that the reference remains unchanged. It uses numerical tolerances for floating-point results. Generated provenance includes available repository, submodule, Python, package, and k6 version information. Archived runs that predate k6-version capture may report a missing version; do not infer or fill in a historical version.

## 5. Re-running the load experiment

The archived-data analysis does not start k6 and does not need it. New data collection does: k6 is an external executable, and a run also requires the queueing application and its configured workload. The `experiments/queueing_demo` application is a separate generic demonstration; the research profile runner and workload are under `runs/`.

The research runner accepts a unique run ID and one of `original`, `a`, `b`, or `c` as the profile. For example:

```powershell
python runs/run_queue_service_time_profile.py queue-demo-new-profile-a-rep-004 --profile a
```

Use a new run ID for every acquisition; the runner writes records under `runs/<run_id>/`. Check the runner's `--help` output and the experiment guide before collecting data. The analyzer uses an explicit, committed set of training and held-out run IDs; merely creating a new run does not add it to that analysis. Update the analysis selection and tests deliberately before treating newly collected runs as a replacement or extension of the published result.

The workflow in `.github/workflows/reproduce-queue-analysis.yml` checks out the repository with its submodule, configures Python from `.python-version`, installs `requirements-test.txt`, and runs the reproduction and repository-hygiene tests. It does not install or invoke k6 because it analyzes archived data.

## Reproducibility scope

Reproducing the archived analysis means rerunning the specified calculations on the committed records with the pinned Python environment and source revisions. It does not independently validate the original load generator, host performance, or queue implementation. Repeating the physical experiment can introduce variation from the host, operating system, process scheduling, and k6 capacity; retain the full run outputs and provenance for any such repetition.
