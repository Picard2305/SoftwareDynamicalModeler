# Local queueing experiment implementation report

## Implementation

- Added a standard-library HTTP queueing app, FIFO worker pool, configurable deterministic CPU job processing, bounded shutdown, and module CLI.
- Added fixed-interval telemetry CSV, per-job correlation CSV, run metadata, backlog accounting, and output-directory collision protection.
- Added a k6 open-model four-step workload and a five-second local smoke mode. The script captures request-attempt starts/completions, HTTP outcomes, latency, and dropped iterations without logging response bodies.
- Added usage documentation and focused app, telemetry, CLI, and k6 contract tests. The existing `sdmodeler` code and CLI were not changed.

## Changed files

- `.\experiments\queueing_demo\app.py`
- `.\experiments\queueing_demo\telemetry.py`
- `.\experiments\queueing_demo\__main__.py`
- `.\experiments\queueing_demo\load.js`
- `.\experiments\queueing_demo\README.md`
- `.\tests\test_queueing_demo_app.py`
- `.\tests\test_queueing_demo_telemetry.py`
- `.\tests\test_queueing_demo_cli.py`
- `.\tests\test_queueing_demo_k6.py`
- `.\.zencoder\chats\3f025934-f50a-47b2-a15a-206c57c10e52\plan.md`

## Versions and verification

- Python: 3.14.2.
- pytest: 9.0.2.
- k6: not installed (`k6 version` returned that the command was not recognized); it was not installed.
- `python -m pytest`: **131 passed**, 29 existing PySINDy sparsity warnings.
- `python -m pytest tests/test_queueing_demo_k6.py -q`: **2 passed** after adding request-attempt counter contract coverage. The test was first run before the matching implementation and failed as expected.
- `node --check experiments/queueing_demo/load.js`: passed.
- `python -m experiments.queueing_demo --help`: passed and displayed the documented CLI options.
- Lint/type-check: no command is configured in `pyproject.toml`.

The Python integration tests start the app on a local ephemeral HTTP port and verify enqueue/processing behavior, telemetry schema, UTC timestamp ordering, correlation fields, accepted/completed/backlog invariants, partial-output behavior, and output collision safety.

## k6 smoke status

The k6-driven localhost smoke run could not be executed because k6 is unavailable. Consequently, k6 runtime validation of scenario boundaries, output writing, request/result metrics, open-model timing under deliberately slow responses, and `dropped_iterations` remains outstanding. The dropped-iteration count is **not available**, not zero. JavaScript syntax validation is not a substitute for a k6 runtime test.

After installing k6 v0.34 or later, use a fresh run ID/output directory. Start the app in one PowerShell terminal:

```powershell
$runId = "queue-demo-local-001"
$runRoot = ".\runs\$runId"
python -m experiments.queueing_demo --host 127.0.0.1 --port 8000 --workers 1 --cpu-work-units 100000 --sampling-interval 1 --drain-timeout 5 --run-id $runId --output-dir "$runRoot\app"
```

Then run this in a second PowerShell terminal with the same run ID:

```powershell
$runId = "queue-demo-local-001"
$runRoot = ".\runs\$runId"
New-Item -ItemType Directory -Path "$runRoot\k6" | Out-Null
$k6Version = (k6 version | Select-Object -First 1)
k6 run --out "json=$runRoot\k6\metrics.json" -e "TARGET_URL=http://127.0.0.1:8000/jobs" -e "RUN_ID=$runId" -e "JOB_TYPE=cpu" -e "OUTPUT_DIR=$runRoot\k6" -e "K6_VERSION=$k6Version" -e "SMOKE=1" .\experiments\queueing_demo\load.js
```

Use a new run ID when repeating the example. Stop the app with Ctrl+C after k6 completes. The expected app schema and k6 outputs are described in `.\experiments\queueing_demo\README.md`.
