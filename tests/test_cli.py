import json

import numpy as np
import pandas as pd
import pytest

from sdmodeler.cli import main
from sdmodeler.config import (
    DataQualityConfig,
    DifferentiationConfig,
    DifferentiationMethod,
    FeatureLibraryConfig,
    FeatureLibraryTier,
    IngestionConfig,
    ModeSwitchingConfig,
    OptimizerConfig,
    PipelineConfig,
    PlantControllerSplitConfig,
    ResamplingMethod,
    SegmentationConfig,
    TelemetrySourceConfig,
    TelemetrySourceType,
    Tier1Config,
    ValidationConfig,
    VariableRole,
    VariableSpec,
)


def _make_config() -> PipelineConfig:
    ingestion = IngestionConfig(
        telemetry_source=TelemetrySourceConfig(
            source_type=TelemetrySourceType.CLOUDWATCH_GET_METRIC_DATA,
            region="us-east-1",
            namespace="AWS/EC2",
        ),
        variables=[
            VariableSpec(name="CPU", role=VariableRole.STATE, resampling_method=ResamplingMethod.LINEAR_INTERPOLATION),
            VariableSpec(name="Instances", role=VariableRole.CONTROL, resampling_method=ResamplingMethod.ZERO_ORDER_HOLD),
        ],
        data_quality=DataQualityConfig(),
    )
    return PipelineConfig(
        ingestion=ingestion,
        segmentation=SegmentationConfig(
            min_segment_length_samples=10,
            business_hours_start_utc=0,
            business_hours_end_utc=24,
        ),
        plant_controller_split=PlantControllerSplitConfig(x_variables=["CPU"], u_variables=["Instances"]),
        mode_switching=ModeSwitchingConfig(enabled=False, thresholds=[]),
        feature_library=FeatureLibraryConfig(
            enabled_tiers=[FeatureLibraryTier.TIER_1_LINEAR_DOMAIN], tier1=Tier1Config()
        ),
        differentiation=DifferentiationConfig(method=DifferentiationMethod.FINITE_DIFFERENCE),
        optimizer=OptimizerConfig(sparsity_threshold_grid=[0.001, 0.01]),
        validation=ValidationConfig(horizon_minutes=2.0, acceptance_threshold=1.0),
    )


def _write_canonical_csv(path):
    n = 40
    idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    t = np.arange(n, dtype=float) * 60.0
    cpu = 50.0 * np.exp(-0.001 * t)
    df = pd.DataFrame({"timestamp": idx, "CPU": cpu, "Instances": np.full(n, 2.0)})
    df.to_csv(path, index=False)


def test_cli_run_with_input_csv_writes_output(tmp_path, capsys, monkeypatch):
    config = _make_config()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")

    csv_path = tmp_path / "frame.csv"
    _write_canonical_csv(csv_path)

    output_path = tmp_path / "models.json"

    import sdmodeler.cli as cli_module
    import sdmodeler.identification.workflow as workflow_module
    from sdmodeler.validation import validate_model as actual_validate_model

    validation_calls = []

    def track_validation(*args, **kwargs):
        validation_calls.append(args[0])
        return actual_validate_model(*args, **kwargs)

    monkeypatch.setattr(cli_module, "validate_model", track_validation, raising=False)
    monkeypatch.setattr(workflow_module, "validate_model", track_validation)

    exit_code = main(
        [
            "run",
            "--config",
            str(config_path),
            "--input-csv",
            str(csv_path),
            "--output",
            str(output_path),
            "--allow-unresolved-design-questions",
            "--include-unreliable",
        ]
    )

    assert exit_code == 0
    assert output_path.exists()
    payload = json.loads(output_path.read_text())
    assert len(payload["models"]) > 0
    assert len(validation_calls) == len(payload["models"])

    captured = capsys.readouterr()
    assert "dCPU/dt" in captured.out


def test_cli_run_saves_observed_vs_simulated_png(tmp_path, monkeypatch):
    from PIL import Image
    from matplotlib.figure import Figure

    config = _make_config()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    csv_path = tmp_path / "frame.csv"
    _write_canonical_csv(csv_path)
    monkeypatch.chdir(tmp_path)

    plots = []
    original_savefig = Figure.savefig

    def record_plot(figure, path, *args, **kwargs):
        axes = figure.axes
        plots.append([(line.get_label(), line.get_xdata(), line.get_ydata()) for ax in axes for line in ax.lines])
        return original_savefig(figure, path, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", record_plot)
    assert main([
        "run", "--config", str(config_path), "--input-csv", str(csv_path),
        "--allow-unresolved-design-questions",
    ]) == 0

    output_path = tmp_path / "ise_robustness_threshold.png"
    with Image.open(output_path) as image:
        assert image.format == "PNG"
        image.verify()
    assert len(plots) == 1
    lines = plots[0]
    assert {label for label, _, _ in lines} == {"CPU observed", "CPU simulated"}
    observed = next(values for label, _, values in lines if label == "CPU observed")
    predicted = next(values for label, _, values in lines if label == "CPU simulated")
    original = pd.read_csv(csv_path)
    assert np.array_equal(observed, original["CPU"].to_numpy()[-len(observed):])
    assert predicted[0] == observed[0]
    assert len(predicted) == len(observed)


def test_cli_run_blocks_on_unresolved_design_questions(tmp_path):
    config = _make_config()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")

    csv_path = tmp_path / "frame.csv"
    _write_canonical_csv(csv_path)

    with pytest.raises(ValueError):
        main(["run", "--config", str(config_path), "--input-csv", str(csv_path)])


def test_cli_run_requires_ingestion_window_without_input_csv(tmp_path):
    config = _make_config()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")

    with pytest.raises(ValueError):
        main(["run", "--config", str(config_path), "--allow-unresolved-design-questions"])


def test_save_validation_plot_skips_non_applicable_result(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from sdmodeler.plotting import save_validation_plot
    from sdmodeler.validation import ValidationResult

    monkeypatch.chdir(tmp_path)
    save_validation_plot(SimpleNamespace(x_variables=[]), ValidationResult(applicable=False))

    assert not (tmp_path / "ise_robustness_threshold.png").exists()
    assert capsys.readouterr().out == ""


def test_build_parser_maps_run_options_and_defaults():
    from sdmodeler.cli import build_parser, run_command

    args = build_parser().parse_args([
        "run",
        "--config", "settings.json",
        "--input-csv", "telemetry.csv",
        "--start", "2024-01-01T00:00:00Z",
        "--end", "2024-01-02T00:00:00Z",
        "--target-interval-seconds", "60",
        "--deploy-boundaries", "boundaries.json",
        "--output", "models.json",
        "--include-unreliable",
        "--allow-unresolved-design-questions",
    ])

    assert args.command == "run"
    assert args.func is run_command
    assert args.config == "settings.json"
    assert args.input_csv == "telemetry.csv"
    assert args.start == "2024-01-01T00:00:00Z"
    assert args.end == "2024-01-02T00:00:00Z"
    assert args.target_interval_seconds == 60.0
    assert args.deploy_boundaries == "boundaries.json"
    assert args.output == "models.json"
    assert args.include_unreliable is True
    assert args.allow_unresolved_design_questions is True

    defaults = build_parser().parse_args(["run", "--config", "settings.json", "--input-csv", "telemetry.csv"])
    assert defaults.include_unreliable is False
    assert defaults.allow_unresolved_design_questions is False
    assert defaults.output is None


def test_run_command_reports_empty_model_set_without_exporting(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    import sdmodeler.cli as cli_module

    config = _make_config()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setattr(cli_module, "_build_frame", lambda _config, _args: pd.DataFrame())
    monkeypatch.setattr(cli_module, "_load_deploy_boundaries", lambda _path: [])
    monkeypatch.setattr(cli_module, "run_fitting_workflow", lambda *args, **kwargs: [])
    args = SimpleNamespace(
        config=str(config_path),
        allow_unresolved_design_questions=True,
        input_csv="telemetry.csv",
        start=None,
        end=None,
        target_interval_seconds=None,
        deploy_boundaries=None,
        include_unreliable=False,
        output=None,
    )

    assert cli_module.run_command(args) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No models to report" in captured.err
