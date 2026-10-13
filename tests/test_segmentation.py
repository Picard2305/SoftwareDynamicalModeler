import numpy as np
import pandas as pd
import pytest

from sdmodeler.config import RegimeDetectionMethod, SegmentationConfig
from sdmodeler.data.ingestion import REGIME_LABEL_COLUMN
from sdmodeler.data.segmentation import (
    DEPLOY_ID_COLUMN,
    DeployBoundary,
    assign_deploy_ids,
    assign_regime_labels,
    fittable_segments,
    segment,
)


def _make_df(n_hours=48, freq="h"):
    idx = pd.date_range("2024-01-01", periods=n_hours, freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "CPU": np.linspace(10, 90, n_hours),
            REGIME_LABEL_COLUMN: [np.nan] * n_hours,
        },
        index=idx,
    )


def test_assign_deploy_ids_splits_at_boundary():
    df = _make_df(n_hours=4)
    boundaries = [
        DeployBoundary(deploy_id="v1", start=df.index[0]),
        DeployBoundary(deploy_id="v2", start=df.index[2]),
    ]
    out = assign_deploy_ids(df, boundaries)
    assert list(out[DEPLOY_ID_COLUMN]) == ["v1", "v1", "v2", "v2"]


def test_assign_deploy_ids_no_boundaries_gives_none():
    df = _make_df(n_hours=3)
    out = assign_deploy_ids(df, [])
    assert out[DEPLOY_ID_COLUMN].isna().all()


def test_assign_regime_labels_time_of_day():
    df = _make_df(n_hours=24)
    config = SegmentationConfig(
        regime_detection_method=RegimeDetectionMethod.TIME_OF_DAY_RULE,
        business_hours_start_utc=9,
        business_hours_end_utc=17,
    )
    out = assign_regime_labels(df, config)
    assert out[REGIME_LABEL_COLUMN].iloc[10] == "business_hours"
    assert out[REGIME_LABEL_COLUMN].iloc[2] == "off_hours"


def test_assign_regime_labels_requires_business_hours_config():
    df = _make_df(n_hours=5)
    config = SegmentationConfig(regime_detection_method=RegimeDetectionMethod.TIME_OF_DAY_RULE)
    with pytest.raises(ValueError):
        assign_regime_labels(df, config)


def test_assign_regime_labels_changepoint_not_implemented():
    df = _make_df(n_hours=5)
    config = SegmentationConfig(regime_detection_method=RegimeDetectionMethod.CHANGEPOINT_DETECTION)
    with pytest.raises(NotImplementedError):
        assign_regime_labels(df, config)


def test_segment_splits_by_deploy_and_regime_and_flags_short_segments():
    df = _make_df(n_hours=24)
    config = SegmentationConfig(
        regime_detection_method=RegimeDetectionMethod.TIME_OF_DAY_RULE,
        business_hours_start_utc=9,
        business_hours_end_utc=17,
        min_segment_length_samples=3,
    )
    segments = segment(df, config, state_variables=["CPU"], deploy_boundaries=[])

    assert all(s.metadata.deploy_id is None for s in segments)
    total_samples = sum(s.metadata.sample_count for s in segments)
    assert total_samples == 24

    off_hours_segments = [s for s in segments if s.metadata.regime_label == "off_hours"]
    business_hours_segments = [s for s in segments if s.metadata.regime_label == "business_hours"]
    assert len(off_hours_segments) >= 1
    assert len(business_hours_segments) >= 1


def test_segment_flags_insufficient_dynamic_range():
    idx = pd.date_range("2024-01-01", periods=10, freq="h", tz="UTC")
    df = pd.DataFrame({"CPU": [50.0] * 10, REGIME_LABEL_COLUMN: [np.nan] * 10}, index=idx)
    config = SegmentationConfig(
        regime_detection_method=RegimeDetectionMethod.TIME_OF_DAY_RULE,
        business_hours_start_utc=0,
        business_hours_end_utc=24,
        min_segment_length_samples=1,
        min_dynamic_range_threshold={"CPU": 0.1},
    )
    segments = segment(df, config, state_variables=["CPU"])
    assert len(segments) == 1
    assert segments[0].metadata.data_quality_flag == "insufficient_dynamic_range"
    assert fittable_segments(segments) == []


def test_fittable_segments_excludes_flagged():
    idx = pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC")
    df = pd.DataFrame({"CPU": [10.0, 90.0], REGIME_LABEL_COLUMN: [np.nan, np.nan]}, index=idx)
    config = SegmentationConfig(
        regime_detection_method=RegimeDetectionMethod.TIME_OF_DAY_RULE,
        business_hours_start_utc=0,
        business_hours_end_utc=24,
        min_segment_length_samples=5,
    )
    segments = segment(df, config, state_variables=["CPU"])
    assert segments[0].metadata.data_quality_flag == "insufficient_length"
    assert fittable_segments(segments) == []
