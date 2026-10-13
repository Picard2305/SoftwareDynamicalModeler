import numpy as np
import pandas as pd
import pytest

from sdmodeler.config import PlantControllerSplitConfig
from sdmodeler.identification.plant_controller import (
    apply_hold_instances_constant,
    autoscaling_frozen_mask,
    build_plant_controller_data,
)


def _make_frame():
    idx = pd.date_range("2024-01-01", periods=5, freq="min", tz="UTC")
    return pd.DataFrame(
        {
            "CPU": [10.0, 20.0, 30.0, 40.0, 50.0],
            "Memory": [1.0, 2.0, 3.0, 4.0, 5.0],
            "Latency": [100.0, 110.0, 120.0, 130.0, 140.0],
            "QueueDepth": [0, 1, 2, 3, 4],
            "RequestRate": [5.0, 6.0, 7.0, 8.0, 9.0],
            "ErrorRate": [0.0, 0.0, 0.1, 0.1, 0.2],
            "Instances": [2, 2, 3, 3, 3],
            "deploy_flag": [0, 0, 0, 0, 0],
            "feature_flag_vector": [0, 0, 0, 0, 0],
        },
        index=idx,
    )


def test_build_plant_controller_data_default_split():
    frame = _make_frame()
    config = PlantControllerSplitConfig()
    data = build_plant_controller_data(frame, config)
    assert data.x.shape == (5, 6)
    assert data.u.shape == (5, 3)
    assert data.x_variables == ["CPU", "Memory", "Latency", "QueueDepth", "RequestRate", "ErrorRate"]
    assert data.u_variables == ["Instances", "deploy_flag", "feature_flag_vector"]


def test_build_plant_controller_data_missing_column_raises():
    frame = _make_frame().drop(columns=["Latency"])
    config = PlantControllerSplitConfig()
    with pytest.raises(ValueError, match="Latency"):
        build_plant_controller_data(frame, config)


def test_build_plant_controller_data_non_numeric_raises():
    frame = _make_frame()
    frame["CPU"] = ["a", "b", "c", "d", "e"]
    config = PlantControllerSplitConfig()
    with pytest.raises(ValueError, match="non-numeric"):
        build_plant_controller_data(frame, config)


def test_autoscaling_frozen_mask():
    frame = _make_frame()
    mask = autoscaling_frozen_mask(frame)
    assert list(mask) == [True, True, False, True, True]


def test_apply_hold_instances_constant_rejects_a_window_with_scaling_transitions():
    frame = _make_frame()
    config = PlantControllerSplitConfig(hold_instances_constant=True)
    with pytest.raises(ValueError, match="Instances"):
        apply_hold_instances_constant(frame, config)


def test_apply_hold_instances_constant_preserves_constant_window():
    frame = _make_frame().iloc[:2]
    config = PlantControllerSplitConfig(hold_instances_constant=True)
    pd.testing.assert_frame_equal(apply_hold_instances_constant(frame, config), frame)


def test_apply_hold_instances_constant_noop_when_disabled():
    frame = _make_frame()
    config = PlantControllerSplitConfig(hold_instances_constant=False)
    filtered = apply_hold_instances_constant(frame, config)
    assert len(filtered) == len(frame)
