from pathlib import Path

from app.models import (
    Prediction,
    PredictionAssetFailureTypeLevel,
    PredictionAssetLevel,
)


def test_prediction_model_matches_prediction_module_contract() -> None:
    assert set(Prediction.__table__.columns.keys()) == {
        "prediction_id",
        "asset_id",
        "job_id",
        "nowcast_time",
        "forecast_time",
    }
    assert set(PredictionAssetLevel.__table__.columns.keys()) == {
        "prediction_asset_id",
        "prediction_id",
        "nowcast_reliability",
        "forecast_reliability",
        "nowcast_virtual_age",
        "forecast_virtual_age",
    }
    assert set(
        PredictionAssetFailureTypeLevel.__table__.columns.keys()
    ) == {
        "prediction_asset_failure_id",
        "prediction_id",
        "asset_failure_type_id",
        "nowcast_failure_type_probability",
        "forecast_failure_type_probability",
    }


def test_prediction_tables_are_not_hypertables() -> None:
    schema_sql = (
        Path(__file__).parents[1] / "db" / "init" / "001_schema.sql"
    ).read_text(encoding="utf-8")

    for table_name in (
        "predictions",
        "prediction_asset_levels",
        "prediction_asset_failure_type_levels",
    ):
        assert f"'public.{table_name}'," not in schema_sql
