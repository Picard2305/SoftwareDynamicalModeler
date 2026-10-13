import json

import pytest
from pydantic import ValidationError

from sdmodeler.config import (
    DataQualityConfig,
    FeatureLibraryConfig,
    FeatureLibraryTier,
    IngestionConfig,
    NonAWSSourceConfig,
    PipelineConfig,
    PlantControllerSplitConfig,
    ResamplingMethod,
    TelemetrySourceConfig,
    TelemetrySourceType,
    Tier2Config,
    VariableRole,
    VariableSpec,
)


def _minimal_ingestion() -> IngestionConfig:
    return IngestionConfig(
        telemetry_source=TelemetrySourceConfig(
            source_type=TelemetrySourceType.CLOUDWATCH_GET_METRIC_DATA,
            region="us-east-1",
            namespace="AWS/EC2",
        ),
        non_aws_sources=[NonAWSSourceConfig(source_name="apm")],
        variables=[
            VariableSpec(name="CPU", role=VariableRole.STATE, resampling_method=ResamplingMethod.LINEAR_INTERPOLATION),
            VariableSpec(name="Instances", role=VariableRole.CONTROL, resampling_method=ResamplingMethod.ZERO_ORDER_HOLD),
        ],
        data_quality=DataQualityConfig(),
    )


def test_pipeline_config_builds_with_defaults():
    cfg = PipelineConfig(ingestion=_minimal_ingestion())
    assert cfg.plant_controller_split.x_variables == [
        "CPU", "Memory", "Latency", "QueueDepth", "RequestRate", "ErrorRate",
    ]
    assert cfg.open_design_questions.unresolved() == [
        "telemetry_access_method_confirmed",
        "native_sampling_interval_confirmed",
        "deploy_boundary_source_confirmed",
        "autoscaler_disabled_loadtest_data_available",
        "known_operational_thresholds_seeded",
    ]


def test_production_run_blocked_until_open_questions_resolved():
    cfg = PipelineConfig(ingestion=_minimal_ingestion())
    with pytest.raises(ValueError):
        cfg.assert_ready_for_production_run()


def test_instances_must_be_control_role():
    with pytest.raises(ValidationError):
        IngestionConfig(
            telemetry_source=TelemetrySourceConfig(
                source_type=TelemetrySourceType.CLOUDWATCH_GET_METRIC_DATA
            ),
            variables=[
                VariableSpec(name="Instances", role=VariableRole.STATE, resampling_method=ResamplingMethod.ZERO_ORDER_HOLD),
            ],
        )


def test_control_variable_cannot_use_linear_interpolation():
    with pytest.raises(ValidationError):
        VariableSpec(
            name="Instances",
            role=VariableRole.CONTROL,
            resampling_method=ResamplingMethod.LINEAR_INTERPOLATION,
        )


def test_x_u_split_must_be_disjoint():
    with pytest.raises(ValidationError):
        PlantControllerSplitConfig(x_variables=["CPU"], u_variables=["CPU"])


def test_tier3_cannot_combine_with_other_tiers():
    with pytest.raises(ValidationError):
        FeatureLibraryConfig(
            enabled_tiers=[
                FeatureLibraryTier.TIER_1_LINEAR_DOMAIN,
                FeatureLibraryTier.TIER_3_FOURIER,
            ]
        )


@pytest.mark.parametrize("degree", [0, 3])
def test_tier2_rejects_unsupported_polynomial_degrees(degree):
    with pytest.raises(ValidationError):
        Tier2Config(max_polynomial_degree=degree)


def test_pipeline_config_roundtrips_through_json_file(tmp_path):
    cfg = PipelineConfig(ingestion=_minimal_ingestion())
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg.model_dump(mode="json")), encoding="utf-8")

    loaded = PipelineConfig.from_file(path)

    assert loaded.plant_controller_split.x_variables == cfg.plant_controller_split.x_variables
    assert loaded.open_design_questions.unresolved() == cfg.open_design_questions.unresolved()
