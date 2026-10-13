from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from sdmodeler.config import (
    DataQualityConfig,
    DifferentiationConfig,
    DifferentiationMethod,
    FeatureLibraryConfig,
    FeatureLibraryTier,
    IngestionConfig,
    ModeSwitchingConfig,
    OpenDesignQuestions,
    OptimizerConfig,
    PipelineConfig,
    PlantControllerSplitConfig,
    ResamplingMethod,
    SegmentationConfig,
    TelemetrySourceConfig,
    TelemetrySourceType,
    Tier1Config,
    ValidationConfig,
    VariableRole,
    VariableSpec,
)


def make_episode(start: str, rng: np.random.Generator, holdout: bool = False) -> pd.DataFrame:
    count = 150
    timestamps = pd.date_range(start, periods=count, freq="min", tz="UTC")
    levels = rng.uniform(45, 110, size=10)
    if holdout:
        levels = levels[::-1]
    rate = np.repeat(levels, 15) + rng.normal(0, 1.2, count)
    instances = np.repeat(rng.integers(2, 9, size=6), 25).astype(float)
    cpu = np.empty(count)
    cpu[0] = 32.0
    for index in range(1, count):
        delayed_index = max(0, index - 3)
        target = 0.58 * rate[delayed_index] - 2.2 * instances[delayed_index]
        cpu[index] = np.clip(cpu[index - 1] + 0.2 * (target - cpu[index - 1]), 0, 100)
    measured = np.clip(cpu + rng.normal(0, 0.35, count), 0, 100)
    return pd.DataFrame({"timestamp": timestamps, "CPU": measured, "Instances": instances, "RequestRate": rate})


def make_config() -> PipelineConfig:
    return PipelineConfig(
        ingestion=IngestionConfig(
            telemetry_source=TelemetrySourceConfig(source_type=TelemetrySourceType.CSV_EXPORT),
            variables=[
                VariableSpec(name="CPU", role=VariableRole.STATE, resampling_method=ResamplingMethod.LINEAR_INTERPOLATION),
                VariableSpec(name="Instances", role=VariableRole.CONTROL, resampling_method=ResamplingMethod.ZERO_ORDER_HOLD),
                VariableSpec(name="RequestRate", role=VariableRole.CONTROL, resampling_method=ResamplingMethod.ZERO_ORDER_HOLD),
            ],
            data_quality=DataQualityConfig(),
        ),
        segmentation=SegmentationConfig(
            min_segment_length_samples=30,
            business_hours_start_utc=0,
            business_hours_end_utc=24,
        ),
        plant_controller_split=PlantControllerSplitConfig(x_variables=["CPU"], u_variables=["Instances", "RequestRate"]),
        mode_switching=ModeSwitchingConfig(enabled=False, thresholds=[]),
        feature_library=FeatureLibraryConfig(enabled_tiers=[FeatureLibraryTier.TIER_1_LINEAR_DOMAIN], tier1=Tier1Config()),
        differentiation=DifferentiationConfig(
            method=DifferentiationMethod.SAVITZKY_GOLAY, savgol_window_length=11, savgol_polyorder=3,
        ),
        optimizer=OptimizerConfig(sparsity_threshold_grid=[0.00001, 0.0001, 0.001]),
        validation=ValidationConfig(horizon_minutes=15.0, acceptance_threshold=0.2),
        open_design_questions=OpenDesignQuestions(
            telemetry_access_method_confirmed="csv_export",
            native_sampling_interval_confirmed=True,
            deploy_boundary_source_confirmed=True,
            autoscaler_disabled_loadtest_data_available=False,
            known_operational_thresholds_seeded=True,
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(args.seed)
    starts = ["2024-01-01 00:00:00", "2024-01-01 06:00:00", "2024-01-01 12:00:00"]
    train = pd.concat([make_episode(start, rng) for start in starts], ignore_index=True)
    holdout = make_episode("2024-01-02 00:00:00", rng, holdout=True)
    train.to_csv(output_dir / "synthetic_train.csv", index=False)
    holdout.to_csv(output_dir / "synthetic_holdout.csv", index=False)
    (output_dir / "synthetic_config.json").write_text(
        json.dumps(make_config().model_dump(mode="json"), indent=2) + "\n", encoding="utf-8",
    )
    boundaries = [
        {"deploy_id": f"episode_{index}", "start": pd.Timestamp(start, tz="UTC").isoformat()}
        for index, start in enumerate(starts, 1)
    ]
    (output_dir / "synthetic_boundaries.json").write_text(
        json.dumps(boundaries, indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
