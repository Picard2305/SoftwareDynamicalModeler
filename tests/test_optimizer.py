import numpy as np
import pytest

from sdmodeler.config import OptimizerConfig, OptimizerMethod
from sdmodeler.identification.optimizer import build_optimizer, sweep_sparsity_thresholds


def _linear_system_data(n=200, noise=0.01):
    rng = np.random.default_rng(0)
    x = rng.uniform(-2, 2, size=(n, 2))
    true_coef = np.array([[2.0, 0.0], [0.0, -1.5]])
    dx = x @ true_coef.T + rng.normal(scale=noise, size=(n, 2))
    return x, dx, true_coef


def test_build_optimizer_stlsq():
    config = OptimizerConfig(method=OptimizerMethod.STLSQ)
    opt = build_optimizer(config, threshold=0.1)
    assert opt.threshold == 0.1


def test_build_optimizer_sr3():
    config = OptimizerConfig(method=OptimizerMethod.SR3)
    opt = build_optimizer(config, threshold=0.1)
    assert opt.reg_weight_lam == 0.1


def test_build_optimizer_elastic_net():
    config = OptimizerConfig(method=OptimizerMethod.ELASTIC_NET)
    opt = build_optimizer(config, threshold=0.1)
    assert opt is not None


def test_sweep_sparsity_thresholds_reports_tradeoff_curve():
    x, dx, true_coef = _linear_system_data()
    config = OptimizerConfig(method=OptimizerMethod.STLSQ, sparsity_threshold_grid=[0.01, 0.5, 3.0])
    results = sweep_sparsity_thresholds(x, dx, config)
    assert len(results) == 3
    thresholds = [r.threshold for r in results]
    assert thresholds == [0.01, 0.5, 3.0]
    nonzeros = [r.n_nonzero_terms for r in results]
    assert nonzeros[0] >= nonzeros[-1]


def test_sweep_low_threshold_recovers_true_sparsity_pattern():
    x, dx, true_coef = _linear_system_data()
    config = OptimizerConfig(method=OptimizerMethod.STLSQ, sparsity_threshold_grid=[0.05])
    results = sweep_sparsity_thresholds(x, dx, config)
    coef = results[0].coefficients
    np.testing.assert_allclose(coef, true_coef, atol=0.2)
