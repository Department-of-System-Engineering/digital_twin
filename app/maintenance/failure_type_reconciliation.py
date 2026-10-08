"""Remove obsolete seeded failure data and prepare CMMS identifiers."""

from dataclasses import dataclass

from sqlalchemy import delete, or_, select, text, update

from ..models import (
    AssetFailureType,
    AssetWorksheetList,
    EtaBeta,
    FailureType,
    Gamma,
    PredictionAssetFailureTypeLevel,
    SensorFailureType,
)


_LEGACY_FAILURE_TYPES = (
    (1, "Failure type 1", 1),
    (2, "Failure type 2", 2),
    (3, "Failure type 3", 3),
)


@dataclass(frozen=True)
class LegacyFailureCleanupResult:
    failure_types: int = 0
    asset_failure_types: int = 0
    sensor_failure_types: int = 0
    prediction_levels: int = 0


def cleanup_legacy_seed_failure_types(connection) -> LegacyFailureCleanupResult:
    """Remove the exact dummy failure records formerly shipped in the seed.

    The affected per-failure prediction rows are invalid legacy output: they
    refer to made-up failure causes. Asset-level prediction history and headers
    are preserved.
    """

    signatures = [
        (
            (FailureType.failure_type_id == local_id)
            & (FailureType.failure_type_name == name)
            & (FailureType.failure_cause_id == external_id)
        )
        for local_id, name, external_id in _LEGACY_FAILURE_TYPES
    ]
    failure_type_ids = list(
        connection.execute(
            select(FailureType.failure_type_id).where(or_(*signatures))
        ).scalars()
    )
    if not failure_type_ids:
        return LegacyFailureCleanupResult()

    asset_failure_type_ids = list(
        connection.execute(
            select(AssetFailureType.asset_failure_type_id).where(
                AssetFailureType.failure_type_id.in_(failure_type_ids)
            )
        ).scalars()
    )
    sensor_failure_type_ids = list(
        connection.execute(
            select(SensorFailureType.sensor_failure_type_id).where(
                SensorFailureType.failure_type_id.in_(failure_type_ids)
            )
        ).scalars()
    )

    removed_prediction_levels = 0
    if asset_failure_type_ids:
        prediction_result = connection.execute(
            delete(PredictionAssetFailureTypeLevel).where(
                PredictionAssetFailureTypeLevel.asset_failure_type_id.in_(
                    asset_failure_type_ids
                )
            )
        )
        removed_prediction_levels = max(prediction_result.rowcount or 0, 0)
        connection.execute(
            update(AssetWorksheetList)
            .where(
                AssetWorksheetList.asset_failure_type_id.in_(
                    asset_failure_type_ids
                )
            )
            .values(asset_failure_type_id=None)
        )
        connection.execute(
            delete(EtaBeta).where(
                EtaBeta.asset_failure_type_id.in_(asset_failure_type_ids)
            )
        )
        connection.execute(
            delete(AssetFailureType).where(
                AssetFailureType.asset_failure_type_id.in_(
                    asset_failure_type_ids
                )
            )
        )

    if sensor_failure_type_ids:
        connection.execute(
            delete(Gamma).where(
                Gamma.sensor_failure_type_id.in_(sensor_failure_type_ids)
            )
        )
        connection.execute(
            delete(SensorFailureType).where(
                SensorFailureType.sensor_failure_type_id.in_(
                    sensor_failure_type_ids
                )
            )
        )

    connection.execute(
        delete(FailureType).where(
            FailureType.failure_type_id.in_(failure_type_ids)
        )
    )

    return LegacyFailureCleanupResult(
        failure_types=len(failure_type_ids),
        asset_failure_types=len(asset_failure_type_ids),
        sensor_failure_types=len(sensor_failure_type_ids),
        prediction_levels=removed_prediction_levels,
    )


def configure_cmms_failure_identifiers(connection) -> None:
    """Enforce external-ID uniqueness and repair identity sequences."""

    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "ux_failure_types_failure_cause_id "
            "ON public.failure_types (failure_cause_id) "
            "WHERE failure_cause_id IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "ux_asset_failure_types_asset_failurecause_id "
            "ON public.asset_failure_types (asset_failurecause_id) "
            "WHERE asset_failurecause_id IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "SELECT setval("
            "pg_get_serial_sequence('public.failure_types', 'failure_type_id'), "
            "COALESCE((SELECT max(failure_type_id) FROM public.failure_types), 1), "
            "EXISTS (SELECT 1 FROM public.failure_types))"
        )
    )
    connection.execute(
        text(
            "SELECT setval("
            "pg_get_serial_sequence('public.asset_failure_types', "
            "'asset_failure_type_id'), "
            "COALESCE((SELECT max(asset_failure_type_id) "
            "FROM public.asset_failure_types), 1), "
            "EXISTS (SELECT 1 FROM public.asset_failure_types))"
        )
    )


cleanup_legacy_preparation_failure_types = cleanup_legacy_seed_failure_types
