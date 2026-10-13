import numpy as np
import pandas as pd
import pytest

from sdmodeler.config import FeatureLibraryConfig, FeatureLibraryTier, Tier1Config, Tier2Config, Tier3Config
from sdmodeler.identification.feature_library import (
    build_feature_library,
    build_feature_row,
    build_tier1_features,
    build_tier2_features,
    build_tier3_features,
    check_condition_number,
)


def _make_frame(n=20, freq="min"):
    idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "CPU": rng.uniform(10, 90, n),
            "cache_hit_rate": rng.uniform(0.5, 0.99, n),
        },
        index=idx,
    )


def test_build_tier1_features_linear_and_domain_terms():
    frame = _make_frame()
    config = Tier1Config(domain_terms=["1 - cache_hit_rate"])
    features = build_tier1_features(frame, ["CPU"], config)
    assert "CPU" in features.columns
    assert "1 - cache_hit_rate" in features.columns
    np.testing.assert_allclose(features["1 - cache_hit_rate"], 1 - frame["cache_hit_rate"])


def test_build_tier2_features_squares_and_interactions():
    frame = _make_frame()
    frame["Memory"] = np.linspace(1, 2, len(frame))
    config = Tier2Config(max_polynomial_degree=2, include_pairwise_interactions=True)
    features = build_tier2_features(frame, ["CPU", "Memory"], config)
    assert "CPU^2" in features.columns
    assert "Memory^2" in features.columns
    assert "CPU*Memory" in features.columns
    np.testing.assert_allclose(features["CPU^2"], frame["CPU"] ** 2)


def test_tier2_degree_one_omits_quadratic_and_interaction_terms():
    frame = _make_frame()
    frame["Memory"] = np.linspace(1, 2, len(frame))
    config = Tier2Config(max_polynomial_degree=1, include_pairwise_interactions=True)

    features = build_tier2_features(frame, ["CPU", "Memory"], config)

    assert features.empty


def test_build_tier3_features_requires_min_duration():
    frame = _make_frame(n=10, freq="h")
    config = Tier3Config(min_segment_duration_days=7.0)
    with pytest.raises(ValueError, match="Tier 3"):
        build_tier3_features(frame, config)


def test_build_tier3_features_produces_sin_cos_columns():
    frame = _make_frame(n=24 * 8, freq="h")
    config = Tier3Config(fourier_periods=["daily"], min_segment_duration_days=7.0)
    features = build_tier3_features(frame, config)
    assert "sin_daily" in features.columns
    assert "cos_daily" in features.columns
    assert (features["sin_daily"] ** 2 + features["cos_daily"] ** 2).round(6).eq(1.0).all()


def test_check_condition_number_flags_collinearity():
    frame = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [2.0, 4.0, 6.0, 8.0]})
    cond, warning = check_condition_number(frame, threshold=10.0)
    assert warning is not None
    assert "condition number" in warning


def test_check_condition_number_no_warning_below_threshold():
    rng = np.random.default_rng(1)
    frame = pd.DataFrame(rng.normal(size=(50, 3)), columns=["a", "b", "c"])
    cond, warning = check_condition_number(frame, threshold=1e10)
    assert warning is None


def test_build_feature_library_tier1_only():
    frame = _make_frame()
    config = FeatureLibraryConfig(enabled_tiers=[FeatureLibraryTier.TIER_1_LINEAR_DOMAIN], tier1=Tier1Config())
    result = build_feature_library(frame, ["CPU"], config)
    assert "CPU" in result.features.columns


def test_build_feature_library_tier1_plus_tier2():
    frame = _make_frame()
    config = FeatureLibraryConfig(
        enabled_tiers=[FeatureLibraryTier.TIER_1_LINEAR_DOMAIN, FeatureLibraryTier.TIER_2_POLYNOMIAL],
        tier1=Tier1Config(),
        tier2=Tier2Config(),
    )
    result = build_feature_library(frame, ["CPU"], config)
    assert "CPU" in result.features.columns
    assert "CPU^2" in result.features.columns


def test_build_feature_library_condition_check_includes_additional_regressors():
    frame = _make_frame()
    config = FeatureLibraryConfig(
        enabled_tiers=[FeatureLibraryTier.TIER_1_LINEAR_DOMAIN],
        tier1=Tier1Config(),
        condition_number_warning_threshold=10.0,
    )

    result = build_feature_library(
        frame,
        ["CPU"],
        config,
        additional_features=frame[["CPU"]].to_numpy(),
    )

    assert result.condition_number_warning is not None
    assert list(result.features.columns) == ["CPU"]


def test_build_feature_row_matches_batch_feature_order():
    frame = _make_frame()
    frame["Instances"] = np.linspace(1.0, 4.0, len(frame))
    x_variables = ["CPU", "cache_hit_rate"]
    u_variables = ["Instances"]
    config = FeatureLibraryConfig(
        enabled_tiers=[FeatureLibraryTier.TIER_1_LINEAR_DOMAIN, FeatureLibraryTier.TIER_2_POLYNOMIAL],
        tier1=Tier1Config(domain_terms=["CPU * Instances"]),
        tier2=Tier2Config(),
    )
    row_index = 5
    features = build_feature_library(frame, x_variables, config).features

    actual = build_feature_row(
        frame[x_variables].iloc[row_index].to_numpy(),
        frame[u_variables].iloc[row_index].to_numpy(),
        x_variables,
        u_variables,
        config,
    )
    expected = np.concatenate(
        [features.iloc[row_index].to_numpy(dtype=float), frame[u_variables].iloc[row_index].to_numpy(dtype=float)]
    )

    np.testing.assert_allclose(actual, expected)


def test_build_feature_library_tier2_without_config_raises():
    frame = _make_frame()
    config = FeatureLibraryConfig(
        enabled_tiers=[FeatureLibraryTier.TIER_1_LINEAR_DOMAIN, FeatureLibraryTier.TIER_2_POLYNOMIAL],
        tier1=Tier1Config(),
        tier2=None,
    )
    with pytest.raises(ValueError, match="Tier 2"):
        build_feature_library(frame, ["CPU"], config)
