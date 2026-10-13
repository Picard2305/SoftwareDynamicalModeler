import numpy as np
import pytest

from sdmodeler.config import DifferentiationConfig, DifferentiationMethod
from sdmodeler.identification.differentiation import differentiate


def _sine_data(n=200):
    t = np.linspace(0, 2 * np.pi, n)
    x = np.sin(t)
    true_dx = np.cos(t)
    return t, x, true_dx


def test_finite_difference_reasonably_accurate():
    t, x, true_dx = _sine_data()
    config = DifferentiationConfig(method=DifferentiationMethod.FINITE_DIFFERENCE)
    result = differentiate(x, t, config)
    assert result.method == DifferentiationMethod.FINITE_DIFFERENCE
    rmse = np.sqrt(np.mean((result.dx - true_dx) ** 2))
    assert rmse < 0.05


def test_savitzky_golay_reasonably_accurate_and_reports_params():
    t, x, true_dx = _sine_data()
    config = DifferentiationConfig(method=DifferentiationMethod.SAVITZKY_GOLAY, savgol_window_length=11, savgol_polyorder=3)
    result = differentiate(x, t, config)
    rmse = np.sqrt(np.mean((result.dx - true_dx) ** 2))
    assert rmse < 0.05
    assert result.params["order"] == 3
    assert "left" in result.params and "right" in result.params


def test_differentiate_multivariate_state():
    t, x1, _ = _sine_data()
    x2 = x1 * 2
    x = np.column_stack([x1, x2])
    config = DifferentiationConfig(method=DifferentiationMethod.FINITE_DIFFERENCE)
    result = differentiate(x, t, config)
    assert result.dx.shape == x.shape
    np.testing.assert_allclose(result.dx[:, 1], result.dx[:, 0] * 2, atol=1e-6)


def test_unknown_method_raises():
    t, x, _ = _sine_data()
    config = DifferentiationConfig(method=DifferentiationMethod.SPLINE)
    result = differentiate(x, t, config)
    assert result.method == DifferentiationMethod.SPLINE
