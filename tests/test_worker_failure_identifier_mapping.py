import os
import sys
from types import ModuleType

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("CMMS_BASE_URL", "http://cmms.test")
os.environ.setdefault("CMMS_TOKEN", "test-token")
os.environ.setdefault("INBOUND_API_KEY", "test-key")

prediction_module = ModuleType("prediction_module")
prediction_core = ModuleType("prediction_module.core")
prediction_core.run_prediction = lambda **_: None  # type: ignore[attr-defined]
prediction_module.core = prediction_core  # type: ignore[attr-defined]
sys.modules.setdefault("prediction_module", prediction_module)
sys.modules.setdefault("prediction_module.core", prediction_core)

from app.maintenance.worker import resolve_asset_failurecause_ids
from app.models import Asset, AssetFailureType, Base, FailureType


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            Asset.__table__,
            FailureType.__table__,
            AssetFailureType.__table__,
        ],
    )
    session = Session(engine)
    session.add(Asset(asset_id=2, asset_key="preparation"))
    session.add_all(
        [
            FailureType(failure_type_id=9, failure_cause_id=9),
            FailureType(failure_type_id=10, failure_cause_id=10),
            FailureType(failure_type_id=11, failure_cause_id=11),
        ]
    )
    session.add_all(
        [
            AssetFailureType(
                asset_failure_type_id=115,
                asset_id=2,
                failure_type_id=9,
                asset_failurecause_id=15,
            ),
            AssetFailureType(
                asset_failure_type_id=116,
                asset_id=2,
                failure_type_id=10,
                asset_failurecause_id=16,
            ),
            AssetFailureType(
                asset_failure_type_id=117,
                asset_id=2,
                failure_type_id=11,
                asset_failurecause_id=17,
            ),
        ]
    )
    session.commit()
    return session


def test_prediction_relation_ids_are_resolved_to_cmms_relation_ids() -> None:
    session = _session()

    result = resolve_asset_failurecause_ids(
        session=session,
        asset_id=2,
        asset_failure_type_ids=[117, 115, 116],
    )

    assert result == [17, 15, 16]


def test_generic_failure_type_ids_are_not_misinterpreted_as_relation_ids() -> None:
    session = _session()

    with pytest.raises(
        ValueError,
        match=r"asset_failure_type_ids=\[9, 10, 11\]",
    ):
        resolve_asset_failurecause_ids(
            session=session,
            asset_id=2,
            asset_failure_type_ids=[9, 10, 11],
        )
