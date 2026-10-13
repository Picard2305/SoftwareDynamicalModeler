import json

import numpy as np
import pandas as pd
import pytest

from sdmodeler.config import DivergenceMetric, FeatureLibraryConfig, FeatureLibraryTier, Tier1Config
from sdmodeler.identification.fitting import CandidateResult, FitResult, FittedModel
from sdmodeler.validation import ValidationResult
from sdmodeler.reporting import (
    build_model_report,
    compare_models,
    export_fit_results,
    format_equations,
    model_to_dict,
    reliable_candidates,
    reportable_candidates,
)
from sdmodeler.data.segmentation import SegmentMetadata


def _make_model(
    deploy_id="v1",
    regime_label="business_hours",
    mode_label="default",
    coefficients=None,
    feature_names=None,
    is_reliable=True,
) -> FittedModel:
    coefficients = coefficients if coefficients is not None else np.array([[-0.02, 0.0, 0.5]])
    feature_names = feature_names or ["CPU", "Memory", "u:Instances"]
    return FittedModel(
        coefficients=coefficients,
        feature_names=feature_names,
        x_variables=["CPU"],
        u_variables=["Instances"],
        library_tiers=[FeatureLibraryTier.TIER_1_LINEAR_DOMAIN],
        condition_number=10.0,
        condition_number_warning=None,
        differentiation_method="finite_difference",
        differentiation_params={},
        optimizer_method="stlsq",
        sparsity_threshold=0.01,
        n_nonzero_terms=int(np.count_nonzero(coefficients)),
        segment_metadata=SegmentMetadata(
            deploy_id=deploy_id,
            regime_label=regime_label,
            start=pd.Timestamp("2024-01-01", tz="UTC"),
            end=pd.Timestamp("2024-01-02", tz="UTC"),
            sample_count=100,
        ),
        mode_label=mode_label,
        sample_count=100,
        r_squared=0.95,
        validation_score=0.02,
        is_reliable=is_reliable,
        feature_library_config=FeatureLibraryConfig(tier1=Tier1Config()),
        validation_metric=DivergenceMetric.NORMALIZED_RMSE,
    )


def _candidate_result(model: FittedModel) -> CandidateResult:
    return CandidateResult(
        model=model,
        validation=ValidationResult(applicable=True, is_reliable=model.is_reliable),
    )


def test_format_equations_omits_zero_terms():
    model = _make_model(coefficients=np.array([[-0.02, 0.0, 0.5]]))
    equations = format_equations(model)
    assert len(equations) == 1
    assert "CPU" in equations[0]
    assert "Memory" not in equations[0]
    assert "u:Instances" in equations[0]
    assert equations[0].startswith("dCPU/dt = ")


def test_format_equations_all_zero_renders_zero():
    model = _make_model(coefficients=np.array([[0.0, 0.0, 0.0]]))
    equations = format_equations(model)
    assert equations == ["dCPU/dt = 0"]


def test_build_model_report_renders_without_error():
    model = _make_model()
    report = build_model_report(model)
    text = report.render()
    assert "CPU" in text
    assert "business_hours" in text
    assert "v1" in text


def test_reliable_candidates_filters_unreliable():
    reliable = _make_model(is_reliable=True)
    unreliable = _make_model(is_reliable=False)
    fit_results = [
        FitResult(
            segment_metadata=reliable.segment_metadata,
            mode_segment=None,
            candidate_results=[_candidate_result(reliable), _candidate_result(unreliable)],
        )
    ]
    filtered = reliable_candidates(fit_results)
    assert filtered == [reliable]


def test_reportable_candidates_include_unvalidated_fourier_models():
    model = _make_model(is_reliable=None)
    model.library_tiers = [FeatureLibraryTier.TIER_3_FOURIER]
    model.validation_score = None
    model.validation_metric = None
    fit_results = [
        FitResult(
            segment_metadata=model.segment_metadata,
            mode_segment=None,
            candidate_results=[_candidate_result(model)],
        )
    ]

    assert reliable_candidates(fit_results) == []
    assert reportable_candidates(fit_results) == [model]


def test_compare_models_reports_coefficient_delta():
    model_a = _make_model(deploy_id="v1", coefficients=np.array([[-0.02, 0.0, 0.5]]))
    model_b = _make_model(deploy_id="v2", coefficients=np.array([[-0.05, 0.0, 0.5]]))
    comparison = compare_models(model_a, model_b)
    assert comparison.deploy_id_a == "v1"
    assert comparison.deploy_id_b == "v2"
    assert not comparison.warnings
    cpu_delta = next(d for d in comparison.deltas if d.feature_name == "CPU")
    assert np.isclose(cpu_delta.delta, -0.03)


def test_compare_models_warns_on_same_deploy_id():
    model_a = _make_model(deploy_id="v1")
    model_b = _make_model(deploy_id="v1")
    comparison = compare_models(model_a, model_b)
    assert any("same deploy_id" in w for w in comparison.warnings)


def test_compare_models_handles_mismatched_feature_sets():
    model_a = _make_model(feature_names=["CPU", "Memory"], coefficients=np.array([[-0.02, 0.1]]))
    model_b = _make_model(feature_names=["CPU", "u:Instances"], coefficients=np.array([[-0.05, 0.3]]))
    comparison = compare_models(model_a, model_b)
    memory_delta = next(d for d in comparison.deltas if d.feature_name == "Memory")
    assert memory_delta.disappeared
    assert memory_delta.coefficient_b == 0.0
    new_term_delta = next(d for d in comparison.deltas if d.feature_name == "u:Instances")
    assert new_term_delta.appeared
    assert new_term_delta.coefficient_a == 0.0


def test_model_to_dict_is_json_serializable():
    model = _make_model()
    d = model_to_dict(model)
    json.dumps(d)
    assert d["x_variables"] == ["CPU"]
    assert d["segment_metadata"]["deploy_id"] == "v1"


def test_export_fit_results_writes_reliable_only(tmp_path):
    reliable = _make_model(is_reliable=True)
    unreliable = _make_model(is_reliable=False)
    fit_results = [
        FitResult(
            segment_metadata=reliable.segment_metadata,
            mode_segment=None,
            candidate_results=[_candidate_result(reliable), _candidate_result(unreliable)],
        )
    ]
    out_path = tmp_path / "models.json"
    export_fit_results(fit_results, str(out_path))
    payload = json.loads(out_path.read_text())
    assert len(payload["models"]) == 1


def test_export_fit_results_includes_unvalidated_fourier_by_default(tmp_path):
    model = _make_model(is_reliable=None)
    model.library_tiers = [FeatureLibraryTier.TIER_3_FOURIER]
    model.validation_score = None
    model.validation_metric = None
    fit_results = [
        FitResult(
            segment_metadata=model.segment_metadata,
            mode_segment=None,
            candidate_results=[
                CandidateResult(
                    model=model,
                    validation=ValidationResult(applicable=False, reason="Tier 3 is not forward-validated."),
                )
            ],
        )
    ]
    out_path = tmp_path / "models.json"

    export_fit_results(fit_results, str(out_path))

    payload = json.loads(out_path.read_text())
    assert len(payload["models"]) == 1
    assert payload["models"][0]["library_tiers"] == [FeatureLibraryTier.TIER_3_FOURIER.value]
    assert payload["models"][0]["is_reliable"] is None


def test_export_fit_results_reliable_only_false_exports_all(tmp_path):
    reliable = _make_model(is_reliable=True)
    unreliable = _make_model(is_reliable=False)
    fit_results = [
        FitResult(
            segment_metadata=reliable.segment_metadata,
            mode_segment=None,
            candidate_results=[_candidate_result(reliable), _candidate_result(unreliable)],
        )
    ]
    out_path = tmp_path / "models.json"
    export_fit_results(fit_results, str(out_path), reliable_only=False)
    payload = json.loads(out_path.read_text())
    assert len(payload["models"]) == 2


def test_export_round_trip_preserves_library_and_validation_metric(tmp_path):
    from sdmodeler.reporting import model_from_dict

    model = _make_model(feature_names=["CPU", "CPU*Instances", "u:Instances"])
    library = FeatureLibraryConfig(tier1=Tier1Config(domain_terms=["CPU*Instances"]))
    model.feature_library_config = library
    model.validation_metric = DivergenceMetric.NORMALIZED_RMSE
    fit_results = [
        FitResult(
            segment_metadata=model.segment_metadata,
            mode_segment=None,
            candidate_results=[_candidate_result(model)],
        )
    ]
    out_path = tmp_path / "models.json"
    export_fit_results(fit_results, str(out_path))
    payload = json.loads(out_path.read_text())
    restored = model_from_dict(payload["models"][0])
    assert restored.feature_library_config == library
    assert restored.validation_metric == model.validation_metric
    np.testing.assert_array_equal(restored.coefficients, model.coefficients)
    assert restored.feature_names == model.feature_names
    assert restored.segment_metadata == model.segment_metadata


def test_model_to_dict_rejects_missing_library_configuration():
    model = _make_model()
    model.feature_library_config = None
    with pytest.raises(ValueError, match="feature_library_config"):
        model_to_dict(model)
