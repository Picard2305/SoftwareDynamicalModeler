import numpy as np
import pandas as pd
import pytest

from sdmodeler.config import (
    DivergenceMetric,
    FeatureLibraryConfig,
    FeatureLibraryTier,
    Tier1Config,
    ValidationConfig,
)
from sdmodeler.identification.fitting import FittedModel
from sdmodeler.data.segmentation import SegmentMetadata
from sdmodeler.validation import simulate_forward, validate_model


def _make_decay_frame(n=100, freq="min", rate=-0.001, noise=0.0):
    idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    t = np.arange(n, dtype=float) * 60.0
    rng = np.random.default_rng(1)
    x0 = 50.0
    x = x0 * np.exp(rate * t) + rng.normal(scale=noise, size=n)
    return pd.DataFrame({"CPU": x, "Instances": np.full(n, 2.0)}, index=idx)


def _make_model(rate=-0.001, tiers=None) -> FittedModel:
    tiers = tiers or [FeatureLibraryTier.TIER_1_LINEAR_DOMAIN]
    return FittedModel(
        coefficients=np.array([[rate, 0.0]]),
        feature_names=["CPU", "u:Instances"],
        x_variables=["CPU"],
        u_variables=["Instances"],
        library_tiers=tiers,
        condition_number=1.0,
        condition_number_warning=None,
        differentiation_method="finite_difference",
        differentiation_params={},
        optimizer_method="stlsq",
        sparsity_threshold=0.01,
        n_nonzero_terms=1,
        segment_metadata=SegmentMetadata(
            deploy_id=None, regime_label=None, start=pd.Timestamp("2024-01-01", tz="UTC"),
            end=pd.Timestamp("2024-01-01", tz="UTC"), sample_count=100,
        ),
        mode_label="default",
        sample_count=100,
        r_squared=1.0,
    )


def _config():
    return FeatureLibraryConfig(enabled_tiers=[FeatureLibraryTier.TIER_1_LINEAR_DOMAIN], tier1=Tier1Config())


def test_simulate_forward_uses_control_at_the_destination_sample():
    model = _make_model(rate=0.0)
    model.coefficients = np.array([[0.0, 1.0]])
    initial_state = np.array([20.0])
    low_control = np.array([[0.0], [0.0]])
    high_destination_control = np.array([[0.0], [1.0]])

    low_prediction = simulate_forward(model, initial_state, low_control, 60.0, _config())
    high_prediction = simulate_forward(model, initial_state, high_destination_control, 60.0, _config())

    assert high_prediction[0, 0] == low_prediction[0, 0]
    assert high_prediction[1, 0] > low_prediction[1, 0]


def test_simulate_forward_reproduces_exponential_decay():
    model = _make_model(rate=-0.001)
    frame = _make_decay_frame(n=50, rate=-0.001)
    u_traj = frame[["Instances"]].to_numpy(dtype=float)
    dt = 60.0
    predicted = simulate_forward(model, np.array([frame["CPU"].iloc[0]]), u_traj, dt, _config())
    actual = frame["CPU"].to_numpy()
    np.testing.assert_allclose(predicted[:, 0], actual, rtol=1e-3)


def test_validate_model_perfect_model_is_reliable():
    model = _make_model(rate=-0.001)
    frame = _make_decay_frame(n=100, rate=-0.001)
    validation_config = ValidationConfig(
        horizon_minutes=10.0, divergence_metric=DivergenceMetric.NORMALIZED_RMSE, acceptance_threshold=0.05
    )
    result = validate_model(model, frame, _config(), validation_config)
    assert result.applicable
    assert result.is_reliable is True
    assert model.is_reliable is True
    assert model.validation_score < 0.05


def test_validate_model_wrong_rate_is_unreliable():
    model = _make_model(rate=0.002)
    frame = _make_decay_frame(n=100, rate=-0.001)
    validation_config = ValidationConfig(
        horizon_minutes=10.0, divergence_metric=DivergenceMetric.NORMALIZED_RMSE, acceptance_threshold=0.05
    )
    result = validate_model(model, frame, _config(), validation_config)
    assert result.applicable
    assert result.is_reliable is False
    assert model.is_reliable is False


def test_validate_model_insufficient_samples_not_applicable():
    model = _make_model(rate=-0.001)
    frame = _make_decay_frame(n=5, rate=-0.001)
    validation_config = ValidationConfig(horizon_minutes=10.0, acceptance_threshold=0.05)
    result = validate_model(model, frame, _config(), validation_config)
    assert result.applicable is False
    assert model.validation_score is None
    assert model.is_reliable is None


def test_validate_model_tier3_not_applicable():
    model = _make_model(rate=-0.001, tiers=[FeatureLibraryTier.TIER_3_FOURIER])
    frame = _make_decay_frame(n=100, rate=-0.001)
    validation_config = ValidationConfig(horizon_minutes=10.0, acceptance_threshold=0.05)
    result = validate_model(model, frame, _config(), validation_config)
    assert result.applicable is False
    assert "Tier 3" in result.reason


def test_validate_model_divergence_time_metric():
    model = _make_model(rate=0.002)
    frame = _make_decay_frame(n=100, rate=-0.001)
    validation_config = ValidationConfig(
        horizon_minutes=10.0, divergence_metric=DivergenceMetric.TRAJECTORY_DIVERGENCE_TIME, acceptance_threshold=300.0
    )
    result = validate_model(model, frame, _config(), validation_config)
    assert result.applicable
    assert result.score is not None
    assert result.is_reliable is False


def test_validate_model_accepts_subsecond_sampling_against_independent_reference():
    from scipy.stats import expon

    idx = pd.date_range("2024-01-01", periods=40, freq="500ms", tz="UTC")
    reference = expon.sf(np.linspace(0, 19.5, 40), scale=5)
    frame = pd.DataFrame({"CPU": reference, "Instances": np.ones(len(idx))}, index=idx)
    model = _make_model(rate=-0.2)
    result = validate_model(model, frame, _config(), ValidationConfig(horizon_minutes=0.1, acceptance_threshold=0.05))
    assert result.applicable
    assert result.is_reliable


def test_validate_model_score_ignores_initialized_prediction(monkeypatch):
    frame = _make_decay_frame(n=5, rate=0.0)
    actual = frame[["CPU"]].to_numpy(dtype=float)
    first_prediction = actual.copy()
    first_prediction[1:] += 3.0
    second_prediction = first_prediction.copy()
    second_prediction[0] += 100.0
    predictions = [first_prediction, second_prediction]
    monkeypatch.setattr(
        "sdmodeler.validation.simulate_forward",
        lambda *args, **kwargs: predictions.pop(0),
    )
    config = ValidationConfig(
        horizon_minutes=4.0,
        divergence_metric=DivergenceMetric.NORMALIZED_RMSE,
        acceptance_threshold=0.05,
    )

    first_result = validate_model(_make_model(), frame, _config(), config)
    second_result = validate_model(_make_model(), frame, _config(), config)

    assert first_result.score == second_result.score


def test_validate_model_rejects_irregular_sampling():
    frame = _make_decay_frame(n=40)
    frame = frame.drop(frame.index[-3])
    with pytest.raises(ValueError, match="uniform"):
        validate_model(_make_model(), frame, _config(), ValidationConfig(horizon_minutes=5))
