from datetime import datetime
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ.setdefault("CMMS_BASE_URL", "http://cmms.test")
os.environ.setdefault("CMMS_TOKEN", "test-token")
os.environ.setdefault("INBOUND_API_KEY", "test-key")

from app.maintenance.data_sync import (
    DataSyncValidationError,
    reconcile_asset_failure_types,
)
from app.maintenance.failure_type_reconciliation import (
    cleanup_legacy_seed_failure_types,
)
from app.models import (
    Asset,
    AssetFailureType,
    Base,
    EtaBeta,
    FailureType,
    Gamma,
    Prediction,
    PredictionAssetFailureTypeLevel,
    PredictionJob,
    SensorFailureType,
)


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            Asset.__table__,
            FailureType.__table__,
            AssetFailureType.__table__,
            EtaBeta.__table__,
            PredictionJob.__table__,
            Prediction.__table__,
            PredictionAssetFailureTypeLevel.__table__,
            SensorFailureType.__table__,
            Gamma.__table__,
        ],
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE asset_worksheet_lists ("
            "asset_worksheet_list_id BIGINT, "
            "maintenance_end_date DATETIME, "
            "asset_failure_type_id BIGINT)"
        )
    return Session(engine)


def _seed_relations(session: Session) -> None:
    session.add(Asset(asset_id=2, asset_key="preparation"))
    session.add_all(
        [
            FailureType(failure_type_id=10),
            FailureType(failure_type_id=11),
        ]
    )
    session.add_all(
        [
            AssetFailureType(
                asset_failure_type_id=15,
                asset_id=2,
                failure_type_id=10,
                asset_failurecause_id=15,
            ),
            AssetFailureType(
                asset_failure_type_id=16,
                asset_id=2,
                failure_type_id=11,
                asset_failurecause_id=16,
            ),
        ]
    )
    session.add_all(
        [
            EtaBeta(
                eta_beta_id=1,
                asset_failure_type_id=15,
                eta_value=10000,
                beta_value=1,
                learning_time=datetime(2026, 9, 1),
            ),
            EtaBeta(
                eta_beta_id=2,
                asset_failure_type_id=16,
                eta_value=10000,
                beta_value=1,
                learning_time=datetime(2026, 9, 1),
            ),
        ]
    )
    session.commit()


def test_reconciliation_removes_unreferenced_stale_relation_and_parameters() -> None:
    session = _session()
    _seed_relations(session)

    removed = reconcile_asset_failure_types(
        session=session,
        asset_id=2,
        current_asset_failure_type_ids={16},
    )
    session.commit()

    assert removed == [15]
    assert session.get(AssetFailureType, 15) is None
    assert session.get(AssetFailureType, 16) is not None
    assert session.query(EtaBeta).filter_by(asset_failure_type_id=15).count() == 0


def test_reconciliation_preserves_and_reports_historical_prediction_reference() -> None:
    session = _session()
    _seed_relations(session)
    session.add(
        PredictionJob(
            job_id=1,
            workorder_id=1,
            request_hash="hash",
            payload={},
            status="done",
            endpoint_type="asset_predict",
            created_at=datetime(2026, 9, 1),
            updated_at=datetime(2026, 9, 1),
        )
    )
    session.add(
        Prediction(
            prediction_id=1,
            asset_id=2,
            job_id=1,
            nowcast_time=datetime(2026, 9, 1),
            forecast_time=datetime(2026, 9, 2),
        )
    )
    session.add(
        PredictionAssetFailureTypeLevel(
            prediction_asset_failure_id=1,
            prediction_id=1,
            asset_failure_type_id=15,
            nowcast_failure_type_probability=0.1,
            forecast_failure_type_probability=0.2,
        )
    )
    session.commit()

    with pytest.raises(
        DataSyncValidationError,
        match="prediction_asset_failure_type_levels=\\[15\\]",
    ):
        reconcile_asset_failure_types(
            session=session,
            asset_id=2,
            current_asset_failure_type_ids={16},
        )

    assert session.get(AssetFailureType, 15) is not None


def test_legacy_seed_cleanup_removes_dummy_failure_graph() -> None:
    session = _session()
    session.add(Asset(asset_id=2, asset_key="preparation"))
    session.add(
        FailureType(
            failure_type_id=2,
            failure_type_name="Failure type 2",
            failure_cause_id=2,
        )
    )
    session.add(
        AssetFailureType(
            asset_failure_type_id=3,
            asset_id=2,
            failure_type_id=2,
            asset_failurecause_id=3,
        )
    )
    session.add(
        EtaBeta(
            eta_beta_id=1,
            asset_failure_type_id=3,
            eta_value=10000,
            beta_value=1,
            learning_time=datetime(2026, 9, 1),
        )
    )
    session.flush()
    session.connection().exec_driver_sql(
        "INSERT INTO asset_worksheet_lists "
        "(asset_worksheet_list_id, maintenance_end_date, asset_failure_type_id) "
        "VALUES (1, '2026-01-10 10:00:00', 3)"
    )

    removed = cleanup_legacy_seed_failure_types(
        session.connection()
    )
    session.commit()

    assert removed.failure_types == 1
    assert removed.asset_failure_types == 1
    assert removed.sensor_failure_types == 0
    assert session.get(AssetFailureType, 3) is None
    assert session.query(EtaBeta).filter_by(asset_failure_type_id=3).count() == 0
    worksheet_failure_type_id = session.connection().exec_driver_sql(
        "SELECT asset_failure_type_id FROM asset_worksheet_lists"
    ).scalar_one()
    assert worksheet_failure_type_id is None
