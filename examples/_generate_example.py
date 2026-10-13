import json

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

ingestion = IngestionConfig(
    telemetry_source=TelemetrySourceConfig(
        source_type=TelemetrySourceType.CSV_EXPORT,
    ),
    variables=[
        VariableSpec(name="CPU", role=VariableRole.STATE, resampling_method=ResamplingMethod.LINEAR_INTERPOLATION),
        VariableSpec(name="Instances", role=VariableRole.CONTROL, resampling_method=ResamplingMethod.ZERO_ORDER_HOLD),
    ],
    data_quality=DataQualityConfig(),
)

config = PipelineConfig(
    ingestion=ingestion,
    segmentation=SegmentationConfig(
        min_segment_length_samples=10,
        business_hours_start_utc=0,
        business_hours_end_utc=24,
    ),
    plant_controller_split=PlantControllerSplitConfig(x_variables=["CPU"], u_variables=["Instances"]),
    mode_switching=ModeSwitchingConfig(enabled=False, thresholds=[]),
    feature_library=FeatureLibraryConfig(
        enabled_tiers=[FeatureLibraryTier.TIER_1_LINEAR_DOMAIN], tier1=Tier1Config()
    ),
    differentiation=DifferentiationConfig(
        method=DifferentiationMethod.SAVITZKY_GOLAY, savgol_window_length=11, savgol_polyorder=3
    ),
    optimizer=OptimizerConfig(sparsity_threshold_grid=[0.001, 0.01, 0.05, 0.1]),
    validation=ValidationConfig(horizon_minutes=5.0, acceptance_threshold=0.2),
    open_design_questions=OpenDesignQuestions(
        telemetry_access_method_confirmed="csv_export",
        native_sampling_interval_confirmed=True,
        deploy_boundary_source_confirmed=True,
        autoscaler_disabled_loadtest_data_available=False,
        known_operational_thresholds_seeded=True,
    ),
)

with open("examples/config.json", "w", encoding="utf-8") as f:
    json.dump(config.model_dump(mode="json"), f, indent=2)

n = 60
idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
rng = np.random.default_rng(42)
instances = np.full(n, 4.0)

a, b = 0.001, 0.05
dt = 60.0
cpu = np.zeros(n)
cpu[0] = 10.0
for i in range(1, n):
    dcdt = -a * cpu[i - 1] + b * instances[i - 1]
    cpu[i] = cpu[i - 1] + dt * dcdt
cpu = cpu + rng.normal(0, 0.1, size=n)

df = pd.DataFrame({"timestamp": idx, "CPU": cpu, "Instances": instances})
df.to_csv("examples/telemetry.csv", index=False)

print(df.head().to_string())
print("...")
print(df.tail().to_string())
print(f"True dynamics used to generate data: dCPU/dt = -{a}*CPU + {b}*Instances")
