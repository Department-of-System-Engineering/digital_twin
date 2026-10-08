import os

os.environ.setdefault("CMMS_BASE_URL", "http://cmms.test")
os.environ.setdefault("CMMS_TOKEN", "test-token")
os.environ.setdefault("INBOUND_API_KEY", "test-key")

from app.maintenance import data_sync
from app.maintenance.data_sync import (
    ensure_asset_failure_type,
    ensure_failure_type,
    synchronize_failure_causes,
)
from app.models import AssetFailureType, FailureType


class _IdentitySession:
    def __init__(self) -> None:
        self.failure_type: FailureType | None = None
        self.asset_failure_type: AssetFailureType | None = None

    def scalar(self, statement):
        del statement
        return self.asset_failure_type or self.failure_type

    def add(self, instance) -> None:
        if isinstance(instance, FailureType):
            instance.failure_type_id = 101
            self.failure_type = instance
        elif isinstance(instance, AssetFailureType):
            instance.asset_failure_type_id = 201
            self.asset_failure_type = instance

    def flush(self) -> None:
        return None


def test_cmms_failure_cause_is_not_used_as_local_primary_key() -> None:
    session = _IdentitySession()

    local_id = ensure_failure_type(
        session,  # type: ignore[arg-type]
        {"failure_cause_id": 9, "code": "CMMS-9"},
    )

    assert local_id == 101
    assert session.failure_type is not None
    assert session.failure_type.failure_type_id == 101
    assert session.failure_type.failure_cause_id == 9


def test_cmms_asset_failure_cause_is_not_used_as_local_primary_key() -> None:
    session = _IdentitySession()

    local_id = ensure_asset_failure_type(
        session,  # type: ignore[arg-type]
        asset_id=2,
        failure_type_id=101,
        failure_cause={
            "asset_failurecause_id": 15,
            "default_occurrence_probability": 12,
            "severity": 3,
        },
    )

    assert local_id == 201
    assert session.asset_failure_type is not None
    assert session.asset_failure_type.asset_failure_type_id == 201
    assert session.asset_failure_type.asset_failurecause_id == 15
    assert session.asset_failure_type.default_occurrence_probability == 0.12


def test_workorder_mapping_remains_keyed_by_cmms_failure_cause(monkeypatch) -> None:
    monkeypatch.setattr(data_sync, "ensure_failure_type", lambda **_: 101)
    monkeypatch.setattr(data_sync, "ensure_asset_failure_type", lambda **_: 201)
    monkeypatch.setattr(data_sync, "reconcile_asset_failure_types", lambda **_: [])

    mapping = synchronize_failure_causes(
        session=object(),  # type: ignore[arg-type]
        asset_id=2,
        payload={
            "failure_causes": [
                {
                    "failure_cause_id": 9,
                    "asset_failurecause_id": 15,
                    "operation_ids": [],
                }
            ]
        },
    )

    assert mapping == {9: 201}
