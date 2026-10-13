from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from sdmodeler.config import (
    DataQualityConfig,
    IngestionConfig,
    NonAWSSourceConfig,
    ResamplingMethod,
    TelemetrySourceConfig,
    TelemetrySourceType,
    VariableRole,
    VariableSpec,
)
from sdmodeler.data.ingestion import (
    REGIME_LABEL_COLUMN,
    CSVSourceFetcher,
    CloudWatchFetcher,
    apply_missing_data_strategy,
    apply_unit_normalization,
    build_canonical_frame,
    check_clock_skew,
    fetch_all_raw_series,
)


def _ts(*args):
    return pd.Timestamp(datetime(*args, tzinfo=timezone.utc))


def test_check_clock_skew_flags_large_offset():
    s1 = pd.Series([1, 2, 3], index=pd.date_range(_ts(2024, 1, 1, 0, 0, 0), periods=3, freq="min", tz="UTC"))
    s2 = pd.Series([1, 2, 3], index=pd.date_range(_ts(2024, 1, 1, 0, 5, 0), periods=3, freq="min", tz="UTC"))
    warnings = check_clock_skew({"a": s1, "b": s2}, max_skew_seconds=5.0)
    assert len(warnings) == 1


def test_check_clock_skew_silent_within_tolerance():
    s1 = pd.Series([1, 2, 3], index=pd.date_range(_ts(2024, 1, 1, 0, 0, 0), periods=3, freq="min", tz="UTC"))
    s2 = pd.Series([1, 2, 3], index=pd.date_range(_ts(2024, 1, 1, 0, 0, 2), periods=3, freq="min", tz="UTC"))
    warnings = check_clock_skew({"a": s1, "b": s2}, max_skew_seconds=5.0)
    assert warnings == []


def test_build_canonical_frame_zero_order_hold_vs_linear_interpolation():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 0, 4, tzinfo=timezone.utc)

    cpu_raw = pd.Series(
        [10.0, 20.0],
        index=pd.DatetimeIndex([_ts(2024, 1, 1, 0, 0, 0), _ts(2024, 1, 1, 0, 4, 0)], tz="UTC"),
        name="CPU",
    )
    instances_raw = pd.Series(
        [2, 3],
        index=pd.DatetimeIndex([_ts(2024, 1, 1, 0, 0, 0), _ts(2024, 1, 1, 0, 3, 0)], tz="UTC"),
        name="Instances",
    )

    ingestion_config = IngestionConfig(
        telemetry_source=TelemetrySourceConfig(source_type=TelemetrySourceType.CLOUDWATCH_GET_METRIC_DATA),
        variables=[
            VariableSpec(name="CPU", role=VariableRole.STATE, resampling_method=ResamplingMethod.LINEAR_INTERPOLATION),
            VariableSpec(name="Instances", role=VariableRole.CONTROL, resampling_method=ResamplingMethod.ZERO_ORDER_HOLD),
        ],
        data_quality=DataQualityConfig(),
    )

    df = build_canonical_frame(
        raw_series_by_variable={"CPU": cpu_raw, "Instances": instances_raw},
        ingestion_config=ingestion_config,
        target_interval_seconds=60,
        start=start,
        end=end,
    )

    assert REGIME_LABEL_COLUMN in df.columns
    assert df[REGIME_LABEL_COLUMN].isna().all()

    assert df["CPU"].iloc[2] == pytest.approx(15.0)

    assert df["Instances"].iloc[1] == 2
    assert df["Instances"].iloc[3] == 3
    assert df["Instances"].iloc[4] == 3


def test_apply_missing_data_strategy_forward_fills_and_flags():
    idx = pd.date_range(_ts(2024, 1, 1), periods=4, freq="min", tz="UTC")
    df = pd.DataFrame({"CPU": [1.0, np.nan, 3.0, np.nan]}, index=idx)
    variables = [VariableSpec(name="CPU", role=VariableRole.STATE, resampling_method=ResamplingMethod.LINEAR_INTERPOLATION)]
    out = apply_missing_data_strategy(df, variables, DataQualityConfig())
    assert list(out["CPU"]) == [1.0, 1.0, 3.0, 3.0]
    assert list(out["CPU_was_missing"]) == [False, True, False, True]


def test_apply_unit_normalization():
    df = pd.DataFrame({"Memory": [1024.0 ** 3 * 2]})
    out = apply_unit_normalization(df, {"Memory": "bytes_to_gb"})
    assert out["Memory"].iloc[0] == pytest.approx(2.0)


def test_csv_source_fetcher_reads_and_filters_range(tmp_path):
    csv_path = tmp_path / "apm.csv"
    csv_path.write_text(
        "timestamp,error_rate\n"
        "2024-01-01T00:00:00Z,0.01\n"
        "2024-01-01T00:01:00Z,0.02\n"
        "2024-01-01T00:02:00Z,0.03\n"
    )
    fetcher = CSVSourceFetcher(NonAWSSourceConfig(source_name="apm", endpoint_or_path=str(csv_path)))
    series = fetcher.fetch(
        "error_rate",
        start=datetime(2024, 1, 1, 0, 0, 30, tzinfo=timezone.utc),
        end=datetime(2024, 1, 1, 0, 2, tzinfo=timezone.utc),
    )
    assert len(series) == 2
    assert list(series.values) == [0.02, 0.03]


def test_cloudwatch_metric_data_preserves_all_pages_and_dimensions():
    observations = {
        _ts(2024, 1, 1, 0, 0): 13.0,
        _ts(2024, 1, 1, 0, 1): 27.0,
    }
    requested_dimensions = {"InstanceId": "instance-a"}

    class Client:
        def __init__(self):
            self.requests = []
            self.pages = iter([
                {"MetricDataResults": [{"Timestamps": [list(observations)[0]], "Values": [13.0]}], "NextToken": "next"},
                {"MetricDataResults": [{"Timestamps": [list(observations)[1]], "Values": [27.0]}]},
            ])

        def get_metric_data(self, **kwargs):
            self.requests.append(kwargs)
            return next(self.pages)

    client = Client()
    source = TelemetrySourceConfig(
        source_type=TelemetrySourceType.CLOUDWATCH_GET_METRIC_DATA,
        namespace="AWS/EC2",
        metric_dimensions={"CPU": requested_dimensions},
    )
    fetcher = CloudWatchFetcher(source, client=client)
    actual = fetcher.fetch("CPU", min(observations), max(observations))
    pd.testing.assert_series_equal(actual, pd.Series(observations, name="CPU"))
    assert all(
        request["MetricDataQueries"][0]["MetricStat"]["Metric"]["Dimensions"]
        == [{"Name": name, "Value": value} for name, value in requested_dimensions.items()]
        for request in client.requests
    )
    assert client.requests[-1]["NextToken"] == "next"


@pytest.mark.parametrize(
    "source_type",
    [
        TelemetrySourceType.PROMETHEUS,
        TelemetrySourceType.TIMESTREAM,
        TelemetrySourceType.CSV_EXPORT,
    ],
)
def test_live_ingestion_rejects_unsupported_telemetry_source_types(source_type):
    ingestion_config = IngestionConfig(
        telemetry_source=TelemetrySourceConfig(source_type=source_type),
        variables=[
            VariableSpec(
                name="CPU",
                role=VariableRole.STATE,
                resampling_method=ResamplingMethod.LINEAR_INTERPOLATION,
            )
        ],
    )

    with pytest.raises(ValueError, match="No fetcher implemented"):
        fetch_all_raw_series(ingestion_config, _ts(2024, 1, 1), _ts(2024, 1, 2))


def test_cloudwatch_metric_statistics_uses_metric_dimensions():
    requested_dimensions = {"InstanceId": "instance-a"}

    class Client:
        def get_metric_statistics(self, **kwargs):
            assert kwargs["Dimensions"] == [
                {"Name": name, "Value": value} for name, value in requested_dimensions.items()
            ]
            return {"Datapoints": []}

    source = TelemetrySourceConfig(
        source_type=TelemetrySourceType.CLOUDWATCH_GET_METRIC_STATISTICS,
        namespace="AWS/EC2",
        metric_dimensions={"CPU": requested_dimensions},
    )
    fetcher = CloudWatchFetcher(source, client=Client())
    assert fetcher.fetch("CPU", _ts(2024, 1, 1), _ts(2024, 1, 1, 0, 1)).empty
