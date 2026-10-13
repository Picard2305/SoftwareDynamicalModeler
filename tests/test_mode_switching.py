import pandas as pd
import pytest

from sdmodeler.config import ModeSwitchingConfig, ModeThreshold, ThresholdOperator
from sdmodeler.identification.mode_switching import (
    compute_mode_labels,
    evaluate_thresholds,
    split_into_modes,
)


def _make_frame():
    idx = pd.date_range("2024-01-01", periods=6, freq="min", tz="UTC")
    return pd.DataFrame(
        {"CPU": [50, 60, 75, 80, 65, 40], "ErrorRate": [0.0, 0.0, 0.0, 0.06, 0.06, 0.0]},
        index=idx,
    )


def test_evaluate_thresholds():
    frame = _make_frame()
    thresholds = [ModeThreshold(variable="CPU", operator=ThresholdOperator.GREATER_THAN, value=70)]
    result = evaluate_thresholds(frame, thresholds)
    assert list(result.iloc[:, 0]) == [False, False, True, True, False, False]


def test_evaluate_thresholds_missing_variable_raises():
    frame = _make_frame()
    thresholds = [ModeThreshold(variable="Nope", operator=ThresholdOperator.GREATER_THAN, value=1)]
    with pytest.raises(ValueError, match="Nope"):
        evaluate_thresholds(frame, thresholds)


def test_compute_mode_labels_no_thresholds_returns_default():
    frame = _make_frame()
    labels = compute_mode_labels(frame, [])
    assert (labels == "default").all()


def test_split_into_modes_disabled_returns_single_segment():
    frame = _make_frame()
    config = ModeSwitchingConfig(enabled=False, thresholds=[])
    segments = split_into_modes(frame, config)
    assert len(segments) == 1
    assert segments[0].mode_label == "default"
    assert segments[0].sample_count == 6


def test_split_into_modes_creates_contiguous_runs():
    frame = _make_frame()
    config = ModeSwitchingConfig(
        enabled=True,
        thresholds=[ModeThreshold(variable="CPU", operator=ThresholdOperator.GREATER_THAN, value=70)],
    )
    segments = split_into_modes(frame, config)
    assert len(segments) == 3
    assert segments[0].sample_count == 2
    assert segments[1].sample_count == 2
    assert segments[2].sample_count == 2
    total = sum(s.sample_count for s in segments)
    assert total == 6


def test_split_into_modes_multiple_thresholds():
    frame = _make_frame()
    config = ModeSwitchingConfig(
        enabled=True,
        thresholds=[
            ModeThreshold(variable="CPU", operator=ThresholdOperator.GREATER_THAN, value=70),
            ModeThreshold(variable="ErrorRate", operator=ThresholdOperator.GREATER_EQUAL, value=0.05),
        ],
    )
    segments = split_into_modes(frame, config)
    labels = [s.mode_label for s in segments]
    assert len(set(labels)) >= 2
