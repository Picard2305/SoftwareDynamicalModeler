import numpy as np
import pandas as pd
import pytest

from sdmodeler.config import (
    DataQualityConfig,
    DifferentiationConfig,
    DifferentiationMethod,
    FeatureLibraryConfig,
    FeatureLibraryTier,
    IngestionConfig,
    ModeSwitchingConfig,
    OptimizerConfig,
    OptimizerMethod,
    PipelineConfig,
    PlantControllerSplitConfig,
    RegimeDetectionMethod,
    ResamplingMethod,
    SegmentationConfig,
    Tier1Config,
    TelemetrySourceConfig,
    TelemetrySourceType,
    VariableRole,
    VariableSpec,
)
from sdmodeler.data.ingestion import REGIME_LABEL_COLUMN
from sdmodeler.data.segmentation import SegmentMetadata
from sdmodeler.identification.fitting import fit_mode_segment
from sdmodeler.identification.mode_switching import ModeSegment
from sdmodeler.identification.workflow import run_fitting_workflow


def _make_linear_system_frame(n=200, freq="min"):
    idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    t = np.arange(n, dtype=float) * 60.0

    rng = np.random.default_rng(0)
    cpu0, mem0 = 50.0, 50.0
    a = -0.01
    cpu = cpu0 * np.exp(a * t) + rng.normal(scale=0.01, size=n)
    mem = mem0 * np.exp(a * t) + rng.normal(scale=0.01, size=n)

    df = pd.DataFrame(
        {
            "CPU": cpu,
            "Memory": mem,
            "Latency": np.full(n, 100.0) + rng.normal(scale=0.01, size=n),
            "QueueDepth": np.full(n, 1.0) + rng.normal(scale=0.01, size=n),
            "RequestRate": np.full(n, 10.0) + rng.normal(scale=0.01, size=n),
            "ErrorRate": np.full(n, 0.01) + rng.normal(scale=0.001, size=n),
            "Instances": np.full(n, 2),
            "deploy_flag": np.zeros(n),
            "feature_flag_vector": np.zeros(n),
            REGIME_LABEL_COLUMN: [np.nan] * n,
        },
        index=idx,
    )
    return df


def _minimal_pipeline_config() -> PipelineConfig:
    ingestion = IngestionConfig(
        telemetry_source=TelemetrySourceConfig(source_type=TelemetrySourceType.CLOUDWATCH_GET_METRIC_DATA),
        variables=[
            VariableSpec(name="CPU", role=VariableRole.STATE, resampling_method=ResamplingMethod.LINEAR_INTERPOLATION),
            VariableSpec(name="Instances", role=VariableRole.CONTROL, resampling_method=ResamplingMethod.ZERO_ORDER_HOLD),
        ],
        data_quality=DataQualityConfig(),
    )
    return PipelineConfig(
        ingestion=ingestion,
        segmentation=SegmentationConfig(
            regime_detection_method=RegimeDetectionMethod.TIME_OF_DAY_RULE,
            business_hours_start_utc=0,
            business_hours_end_utc=24,
            min_segment_length_samples=20,
            min_dynamic_range_threshold={},
        ),
        plant_controller_split=PlantControllerSplitConfig(),
        mode_switching=ModeSwitchingConfig(enabled=False, thresholds=[]),
        feature_library=FeatureLibraryConfig(
            enabled_tiers=[FeatureLibraryTier.TIER_1_LINEAR_DOMAIN],
            tier1=Tier1Config(),
        ),
        differentiation=DifferentiationConfig(method=DifferentiationMethod.FINITE_DIFFERENCE),
        optimizer=OptimizerConfig(method=OptimizerMethod.STLSQ, sparsity_threshold_grid=[0.001, 0.01, 0.1]),
    )


def test_fit_mode_segment_returns_one_candidate_per_threshold():
    frame = _make_linear_system_frame()
    config = _minimal_pipeline_config()
    mode_seg = ModeSegment(
        mode_label="default", start=frame.index[0], end=frame.index[-1], sample_count=len(frame), frame=frame
    )

    seg_meta = SegmentMetadata(
        deploy_id=None, regime_label=None, start=frame.index[0], end=frame.index[-1], sample_count=len(frame)
    )

    candidates = fit_mode_segment(mode_seg, seg_meta, config)
    assert len(candidates) == 3
    assert {c.sparsity_threshold for c in candidates} == {0.001, 0.01, 0.1}
    for c in candidates:
        assert c.validation_score is None
        assert c.sample_count == len(frame)
        assert c.differentiation_method == "finite_difference"
        assert c.optimizer_method == "stlsq"
        assert not np.isnan(c.r_squared)


def test_fit_mode_segment_too_short_returns_empty():
    frame = _make_linear_system_frame(n=5)
    config = _minimal_pipeline_config()
    mode_seg = ModeSegment(
        mode_label="default", start=frame.index[0], end=frame.index[-1], sample_count=len(frame), frame=frame
    )

    seg_meta = SegmentMetadata(
        deploy_id=None, regime_label=None, start=frame.index[0], end=frame.index[-1], sample_count=len(frame)
    )
    candidates = fit_mode_segment(mode_seg, seg_meta, config)
    assert candidates == []


def test_run_fitting_workflow_end_to_end():
    frame = _make_linear_system_frame(n=100)
    config = _minimal_pipeline_config()
    results = run_fitting_workflow(frame, config, deploy_boundaries=[])
    assert len(results) >= 1
    total_candidates = sum(len(r.candidates) for r in results)
    assert total_candidates > 0
    for r in results:
        assert len(r.candidate_results) == len(r.candidates)
        for candidate_result in r.candidate_results:
            assert any(candidate_result.model is candidate for candidate in r.candidates)
            assert candidate_result.validation is not None
            assert candidate_result.model.mode_label == "default"
            assert candidate_result.model.segment_metadata.deploy_id is None


def test_run_fitting_workflow_skips_low_quality_segments():
    frame = _make_linear_system_frame(n=100)
    frame["CPU"] = 50.0
    config = _minimal_pipeline_config()
    config.segmentation.min_dynamic_range_threshold = {"CPU": 0.5}
    results = run_fitting_workflow(frame, config, deploy_boundaries=[])
    assert results == []


def test_run_fitting_workflow_rejects_missing_control_observations():
    frame = _make_linear_system_frame(n=100)
    frame["Instances"] = np.nan
    with pytest.raises(ValueError, match="Instances"):
        run_fitting_workflow(frame, _minimal_pipeline_config())


def test_frozen_window_selection_excludes_unmarked_samples():
    frame = _make_linear_system_frame(n=100)
    frame["autoscaling_frozen"] = [False] * 40 + [True] * 60
    config = _minimal_pipeline_config()
    config.segmentation.autoscaling_frozen_window_metadata_key = "autoscaling_frozen"
    config.plant_controller_split.filter_to_autoscaling_frozen_windows_only = True
    results = run_fitting_workflow(frame, config)
    assert results
    assert all(result.mode_segment.frame["autoscaling_frozen"].all() for result in results)


def test_frozen_window_selection_requires_metadata():
    config = _minimal_pipeline_config()
    config.segmentation.autoscaling_frozen_window_metadata_key = "autoscaling_frozen"
    config.plant_controller_split.filter_to_autoscaling_frozen_windows_only = True
    with pytest.raises(ValueError, match="autoscaling_frozen"):
        run_fitting_workflow(_make_linear_system_frame(n=100), config)


def test_hold_instances_constant_fits_distinct_contiguous_windows():
    frame = _make_linear_system_frame(n=100)
    frame["Instances"] = [2.0] * 50 + [3.0] * 50
    config = _minimal_pipeline_config()
    config.plant_controller_split.hold_instances_constant = True
    results = run_fitting_workflow(frame, config)
    assert results
    assert all(result.mode_segment.frame["Instances"].nunique() == 1 for result in results)
    assert all(result.mode_segment.frame.index.isin(frame.index).all() for result in results)
    assert set(frame.index) == set().union(*(set(result.mode_segment.frame.index) for result in results))


def test_frozen_window_selection_and_instance_splitting_compose():
    frame = _make_linear_system_frame(n=100)
    frame["autoscaling_frozen"] = [False] * 20 + [True] * 80
    frame["Instances"] = [2.0] * 50 + [3.0] * 50
    config = _minimal_pipeline_config()
    config.segmentation.autoscaling_frozen_window_metadata_key = "autoscaling_frozen"
    config.plant_controller_split.filter_to_autoscaling_frozen_windows_only = True
    config.plant_controller_split.hold_instances_constant = True

    results = run_fitting_workflow(frame, config)

    assert results
    assert all(result.mode_segment.frame["autoscaling_frozen"].all() for result in results)
    assert all(result.mode_segment.frame["Instances"].nunique() == 1 for result in results)
    observed_timestamps = set().union(*(set(result.mode_segment.frame.index) for result in results))
    assert observed_timestamps == set(frame.index[20:])
