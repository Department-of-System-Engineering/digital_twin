import asyncio
import httpx
from dataclasses import dataclass
from datetime import datetime
from collections import Counter

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .cmms import (cmms_get_asset_failure_causes)
from ..models import (
    Asset,
    AssetFailureType,
    AssetWorksheetList,
    EtaBeta,
    FailureType,
    OperationsDoneList,
    PredictionAssetFailureTypeLevel,
)
from .schemas import AssetPredictIn


class DataSyncNotFoundError(Exception):
    """
    Egy szükséges külső vagy helyi adat
    nem található.
    """


class DataSyncValidationError(Exception):
    """
    A kapott adatok szerkezete vagy
    kapcsolata hibás.
    """


@dataclass(frozen=True)
class WorkorderSyncResult:
    """
    A munkalap- és hibaokszinkronizálás eredménye.

    Az asset_id a saját rendszerben generált
    belső assets.asset_id.

    Az asset_failure_cause_operations alakja:

        [
            {
                "asset_failurecause_id": 31,
                "operation_ids": [3, 7, 16],
            },
            {
                "asset_failurecause_id": 32,
                "operation_ids": [8, 11],
            },
        ]
    """

    asset_id: int
    asset_failure_type_id: int | None
    asset_worksheet_list_id: int
    maintenance_end_date: datetime
    asset_failure_cause_operations: list[dict]


def normalize_positive_int(value: object, field_name: str) -> int:
    """
    Pozitív egész számmá alakít egy külső
    rendszerből érkező azonosítót.
    """

    try:
        normalized_value = int(value)
    except (TypeError, ValueError) as error:
        raise DataSyncValidationError(f"{field_name} must be an integer") from error

    if normalized_value <= 0:
        raise DataSyncValidationError(f"{field_name} must be greater than zero")

    return normalized_value


def get_failure_causes(payload: dict) -> list[dict]:
    """
    Ellenőrzi és visszaadja a CMMS-válasz
    failure_causes listáját.
    """

    if not isinstance(payload, dict):
        raise DataSyncValidationError("The CMMS response must be a JSON object")

    failure_causes = payload.get("failure_causes")

    if not isinstance(failure_causes, list):
        raise DataSyncValidationError("failure_causes must be a list")

    normalized_failure_causes: list[dict] = []

    for index, failure_cause in enumerate(failure_causes):
        if not isinstance(failure_cause, dict):
            raise DataSyncValidationError("Every failure cause must be a JSON object; invalid index=" f"{index}")

        normalized_failure_causes.append(failure_cause)

    return normalized_failure_causes


def ensure_asset_id(session: Session, cmms_asset_id: int) -> int:
    """
    A CMMS-től érkező külső asset_id alapján
    visszaadja a saját rendszer belső asset_id
    értékét. Ha az eszköz még nem létezik,
    létrehozza az assets rekordot.

    A CMMS-válasz nem tartalmaz eszköznevet,
    ezért új rekordnál az asset_name NULL marad.
    """

    normalized_cmms_asset_id = normalize_positive_int(
        cmms_asset_id,
        "cmms_asset_id",
    )

    asset_id = session.execute(
        select(Asset.asset_id).where(
            Asset.cmms_asset_id == normalized_cmms_asset_id
        )
    ).scalar_one_or_none()

    if asset_id is not None:
        return int(asset_id)

    asset = Asset(
        cmms_asset_id=normalized_cmms_asset_id,
        asset_name=None,
    )

    session.add(asset)
    session.flush()

    return int(asset.asset_id)


def build_asset_failure_cause_operations(payload: dict) -> list[dict]:
    """
    A CMMS-válaszból elkészíti a predikció
    számára átadandó listát.
    """

    failure_causes = get_failure_causes(payload)

    result: list[dict] = []
    seen_asset_failurecause_ids: set[int] = set()

    for failure_cause in failure_causes:
        if (failure_cause.get("asset_failurecause_id") is None):
            raise DataSyncValidationError("asset_failurecause_id is missing")

        asset_failurecause_id = (normalize_positive_int(failure_cause.get("asset_failurecause_id"), "asset_failurecause_id"))

        if (asset_failurecause_id in seen_asset_failurecause_ids):
            raise DataSyncValidationError("Duplicate asset_failurecause_id: " f"{asset_failurecause_id}")

        seen_asset_failurecause_ids.add(asset_failurecause_id)

        operation_ids_raw = failure_cause.get("operation_ids")

        if not isinstance(operation_ids_raw, list):
            raise DataSyncValidationError("operation_ids must be a list for asset_failurecause_id=" f"{asset_failurecause_id}")

        operation_ids: list[int] = []

        for operation_id_raw in operation_ids_raw:
            operation_ids.append(
                normalize_positive_int(
                    operation_id_raw,
                    (
                        "operation_id for "
                        "asset_failurecause_id="
                        f"{asset_failurecause_id}"
                    ),
                )
            )

        result.append(
            {
                "asset_failurecause_id": (
                    asset_failurecause_id
                ),
                "operation_ids": operation_ids,
            }
        )

    return result


def build_prediction_failure_cause_operations(
    asset_failure_cause_operations: list[dict],
    completed_operation_ids: list[int],
) -> list[dict]:
    """
    Az /asset_predict műveleteit a CMMS-ben
    megadott hibaok-művelet kapcsolatok alapján
    a predikció által várt JSON-struktúrába rendezi.
    """

    normalized_completed_operation_ids = [
        normalize_positive_int(
            operation_id,
            "completed operation_id",
        )
        for operation_id in completed_operation_ids
    ]

    result: list[dict] = []

    for failure_cause in asset_failure_cause_operations:
        allowed_operation_ids = set(
            failure_cause["operation_ids"]
        )

        result.append(
            {
                "asset_failurecause_id": int(
                    failure_cause["asset_failurecause_id"]
                ),
                "operation_ids": [
                    operation_id
                    for operation_id
                    in normalized_completed_operation_ids
                    if operation_id in allowed_operation_ids
                ],
            }
        )

    return result


def ensure_failure_type(
    session: Session,
    failure_cause: dict,
) -> int:
    """
    Létrehozza vagy frissíti a failure_types
    rekordot.

    Leképezés:
        failure_types.failure_type_id = database-generated local ID

        failure_types.failure_cause_id = CMMS failure_cause_id
    """

    if (
        failure_cause.get(
            "failure_cause_id"
        )
        is None
    ):
        raise DataSyncValidationError(
            "failure_cause_id is missing"
        )

    failure_cause_id = normalize_positive_int(
        failure_cause.get(
            "failure_cause_id"
        ),
        "failure_cause_id",
    )

    failure_type_name_raw = failure_cause.get(
        "code"
    )

    failure_type_name = (
        str(failure_type_name_raw)
        if failure_type_name_raw is not None
        else None
    )

    failure_type = session.scalar(
        select(FailureType).where(
            FailureType.failure_cause_id == failure_cause_id
        )
    )

    if failure_type is None:
        failure_type = FailureType(
            failure_type_name=(
                failure_type_name
            ),
            is_preventive=None,
            failure_cause_id=(
                failure_cause_id
            ),
        )

        session.add(
            failure_type
        )

    else:
        failure_type.failure_cause_id = (
            failure_cause_id
        )

        if failure_type_name is not None:
            failure_type.failure_type_name = (
                failure_type_name
            )

    session.flush()

    return int(failure_type.failure_type_id)


def ensure_asset_failure_type(
    session: Session,
    asset_id: int,
    failure_type_id: int,
    failure_cause: dict,
) -> int:
    """
    Létrehozza vagy frissíti az
    asset_failure_types rekordot.

    Leképezés:

        asset_failure_types.asset_id
            = belső assets.asset_id

        asset_failure_types.failure_type_id
            = local failure_types.failure_type_id

        asset_failure_types.asset_failurecause_id
            = CMMS asset_failurecause_id

        asset_failure_types.asset_failure_type_id
            = database-generated local ID
    """

    if (
        failure_cause.get(
            "asset_failurecause_id"
        )
        is None
    ):
        raise DataSyncValidationError(
            "asset_failurecause_id is missing"
        )

    asset_failurecause_id = normalize_positive_int(
        failure_cause.get(
            "asset_failurecause_id"
        ),
        "asset_failurecause_id",
    )

    asset_failure_type = session.scalar(
        select(AssetFailureType).where(
            AssetFailureType.asset_failurecause_id
            == asset_failurecause_id
        )
    )

    if asset_failure_type is None:
        asset_failure_type = AssetFailureType(
            asset_id=int(
                asset_id
            ),
            failure_type_id=int(
                failure_type_id
            ),
            asset_failurecause_id=(
                asset_failurecause_id
            ),
        )

        session.add(
            asset_failure_type
        )

    else:
        if (int(asset_failure_type.asset_id) != int(asset_id)):
            raise DataSyncValidationError(
                "The asset_failurecause_id is "
                "already assigned to another asset: "
                f"{asset_failurecause_id}"
            )

        asset_failure_type.failure_type_id = int(
            failure_type_id
        )

        asset_failure_type.asset_failurecause_id = (
            asset_failurecause_id
        )

    probability_raw = failure_cause.get(
        "default_occurrence_probability"
    )

    if probability_raw is None:
        asset_failure_type.default_occurrence_probability = None
    else:
        try:
            probability = float(probability_raw)
        except (TypeError, ValueError) as error:
            raise DataSyncValidationError(
                "default_occurrence_probability must be numeric "
                f"for asset_failurecause_id={asset_failurecause_id}"
            ) from error

        if not 0.0 <= probability <= 99.0:
            raise DataSyncValidationError(
                "default_occurrence_probability must be between 0 and 99; "
                f"received={probability} for "
                f"asset_failurecause_id={asset_failurecause_id}"
            )

        asset_failure_type.default_occurrence_probability = (
            probability / 100.0
        )

    severity_raw = failure_cause.get(
        "severity"
    )

    if severity_raw is None:
        asset_failure_type.severity = None
    else:
        asset_failure_type.severity = (
            normalize_positive_int(
                severity_raw,
                (
                    "severity for "
                    "asset_failurecause_id="
                    f"{asset_failurecause_id}"
                ),
            )
        )

    session.flush()

    return int(asset_failure_type.asset_failure_type_id)


def reconcile_asset_failure_types(
    session: Session,
    asset_id: int,
    current_asset_failure_type_ids: set[int],
) -> list[int]:
    """Remove CMMS relations that are no longer current and safe to delete.

    Prediction and worksheet references are historical data. They are never
    removed implicitly; synchronization stops with a precise error so an
    operator can decide how that history should be retained.
    """

    stale_query = select(
        AssetFailureType.asset_failure_type_id
    ).where(
        AssetFailureType.asset_id == int(asset_id)
    )

    if current_asset_failure_type_ids:
        stale_query = stale_query.where(
            AssetFailureType.asset_failure_type_id.not_in(
                sorted(current_asset_failure_type_ids)
            )
        )

    stale_ids = sorted(
        int(value)
        for value in session.execute(stale_query).scalars().all()
    )

    if not stale_ids:
        return []

    prediction_references = set(
        int(value)
        for value in session.execute(
            select(
                PredictionAssetFailureTypeLevel.asset_failure_type_id
            )
            .where(
                PredictionAssetFailureTypeLevel.asset_failure_type_id.in_(
                    stale_ids
                )
            )
            .distinct()
        ).scalars().all()
    )
    worksheet_references = set(
        int(value)
        for value in session.execute(
            select(
                AssetWorksheetList.asset_failure_type_id
            )
            .where(
                AssetWorksheetList.asset_failure_type_id.in_(stale_ids)
            )
            .distinct()
        ).scalars().all()
        if value is not None
    )

    referenced_ids = prediction_references | worksheet_references
    if referenced_ids:
        reference_details: list[str] = []
        if prediction_references:
            reference_details.append(
                "prediction_asset_failure_type_levels="
                f"{sorted(prediction_references)}"
            )
        if worksheet_references:
            reference_details.append(
                "asset_worksheet_lists="
                f"{sorted(worksheet_references)}"
            )

        raise DataSyncValidationError(
            "CMMS synchronization found stale asset_failure_types rows "
            f"for asset_id={asset_id}: {stale_ids}. Historical references "
            "prevent automatic removal: "
            + ", ".join(reference_details)
        )

    session.execute(
        delete(EtaBeta).where(
            EtaBeta.asset_failure_type_id.in_(stale_ids)
        )
    )
    session.execute(
        delete(AssetFailureType).where(
            AssetFailureType.asset_id == int(asset_id),
            AssetFailureType.asset_failure_type_id.in_(stale_ids),
        )
    )
    session.flush()

    return stale_ids


def synchronize_failure_causes(
    session: Session,
    asset_id: int,
    payload: dict,
) -> dict[int, int]:
    """
    A CMMS GET-válasz összes hibaokát azonnal
    szinkronizálja a failure_types és
    asset_failure_types táblákba.

    Visszatérési érték:

        {
            CMMS failure_cause_id: local asset_failure_type_id
        }

    The returned keys are CMMS failure-cause IDs because the incoming
    workorder uses that namespace. Values are local asset/failure relation IDs.
    """

    failure_causes = get_failure_causes(
        payload
    )

    failure_type_mapping: dict[int, int] = {}

    for failure_cause in failure_causes:
        cmms_failure_cause_id = normalize_positive_int(
            failure_cause.get("failure_cause_id"),
            "failure_cause_id",
        )
        failure_type_id = ensure_failure_type(
            session=session,
            failure_cause=failure_cause,
        )

        if (
            cmms_failure_cause_id
            in failure_type_mapping
        ):
            raise DataSyncValidationError(
                "Duplicate failure_cause_id in the CMMS response: "
                f"{cmms_failure_cause_id}"
            )

        asset_failure_type_id = (
            ensure_asset_failure_type(
                session=session,
                asset_id=asset_id,
                failure_type_id=(
                    failure_type_id
                ),
                failure_cause=failure_cause,
            )
        )

        failure_type_mapping[
            cmms_failure_cause_id
        ] = asset_failure_type_id

    reconcile_asset_failure_types(
        session=session,
        asset_id=asset_id,
        current_asset_failure_type_ids=set(
            failure_type_mapping.values()
        ),
    )

    return failure_type_mapping


def resolve_workorder_asset_failure_type_id(
    workorder: AssetPredictIn,
    failure_type_mapping: dict[int, int],
) -> int | None:
    """
    Meghatározza, hogy a munkalap melyik
    asset_failure_types rekordhoz kapcsolódjon.

    Preventív munkalap:
        NULL

    Nem preventív munkalap:
        a beérkező failure_cause_id alapján
        feloldott asset_failure_type_id
    """

    workorder_type = (
        workorder.type.strip().upper()
    )

    if workorder_type == "PREVENTIVE":
        return None

    if workorder.failure_cause_id is None:
        raise DataSyncValidationError(
            "failure_cause_id is required for "
            "a non-preventive workorder"
        )

    failure_type_id = int(
        workorder.failure_cause_id
    )

    asset_failure_type_id = (
        failure_type_mapping.get(
            failure_type_id
        )
    )

    if asset_failure_type_id is None:
        raise DataSyncNotFoundError(
            "The workorder failure_cause_id was "
            "not returned by the CMMS: "
            f"{failure_type_id}"
        )

    return asset_failure_type_id


def store_asset_worksheet(
    session: Session,
    workorder: AssetPredictIn,
    asset_id: int,
    asset_failure_type_id: int | None,
) -> AssetWorksheetList:
    """
    Meglévő munkalapot ad vissza vagy új
    asset_worksheet_lists rekordot készít.

    Természetes azonosítás:

        asset_id
        maintenance_end_date
        failure_start_time
    """

    existing_worksheets = session.execute(
        select(AssetWorksheetList
               ).where(AssetWorksheetList.asset_id == int(asset_id),
                       AssetWorksheetList.maintenance_end_date == workorder.ended,
                       AssetWorksheetList.failure_start_time == workorder.failure_date,
                       )
    ).scalars().all()

    if len(existing_worksheets) > 1:
        raise DataSyncValidationError(
            "Multiple asset worksheets were found "
            "for the same asset and timestamps"
        )

    if existing_worksheets:
        worksheet = existing_worksheets[0]

        stored_asset_failure_type_id = (
            worksheet.asset_failure_type_id
        )

        if (stored_asset_failure_type_id != asset_failure_type_id):
            raise DataSyncValidationError(
                "The existing asset worksheet has "
                "a different asset_failure_type_id"
            )

        return worksheet

    worksheet = AssetWorksheetList(
        asset_id=int(
            asset_id
        ),
        maintenance_end_date=(
            workorder.ended
        ),
        source_sys_time=(
            workorder.ended
        ),
        asset_failure_type_id=(
            asset_failure_type_id
        ),
        failure_start_time=(
            workorder.failure_date
        ),
        downtime_in_min=None,
    )

    session.add(
        worksheet
    )

    session.flush()

    return worksheet


def store_cmms_operations(
    session: Session,
    operation_ids: list[int],
    worksheet: AssetWorksheetList,
) -> None:
    """
    A CMMS failure_causes[].operation_ids
    minden elemét külön operations_done_lists
    rekordként menti.

    Ugyanaz az operation_template_id többször is
    szerepelhet. Ebben az esetben minden előfordulás
    külön adatbázisrekordot jelent.

    Ismételt jobfeldolgozáskor csak a hiányzó
    előfordulásokat hozza létre.
    """

    normalized_operation_ids = [
        normalize_positive_int(
            operation_id,
            "CMMS operation_id",
        )
        for operation_id in operation_ids
    ]

    desired_counts = Counter(
        normalized_operation_ids
    )

    existing_operation_ids = (session.execute(select(OperationsDoneList.operation_template_id).where(
        OperationsDoneList.asset_worksheet_list_id == int(worksheet.asset_worksheet_list_id),
        OperationsDoneList.maintenance_end_date == worksheet.maintenance_end_date,)).scalars().all())

    existing_counts = Counter(
        int(operation_id)
        for operation_id
        in existing_operation_ids
    )

    # Ha az adatbázisban több előfordulás van,
    # mint amennyit az aktuális workorder tartalmaz,
    # akkor nem lehet eldönteni, melyik rekordot
    # kellene törölni. Ezért hibát jelezünk.
    inconsistent_counts = {
        operation_id: {
            "stored": stored_count,
            "received": desired_counts.get(
                operation_id,
                0,
            ),
        }
        for (
            operation_id,
            stored_count,
        ) in existing_counts.items()
        if (stored_count > desired_counts.get(operation_id, 0,))
    }

    if inconsistent_counts:
        raise DataSyncValidationError(
            "The stored operations do not match "
            "the received workorder operations: "
            f"{inconsistent_counts}"
        )

    for (
        operation_id,
        desired_count,
    ) in desired_counts.items():
        existing_count = existing_counts.get(
            operation_id,
            0,
        )

        missing_count = (desired_count - existing_count)

        for _ in range(
            missing_count
        ):
            operation = OperationsDoneList(
                operation_template_id=(
                    operation_id
                ),
                asset_worksheet_list_id=int(
                    worksheet
                    .asset_worksheet_list_id
                ),
                maintenance_end_date=(
                    worksheet
                    .maintenance_end_date
                ),
            )

            session.add(
                operation
            )

    session.flush()


def synchronize_workorder(
    session: Session,
    workorder: AssetPredictIn,
) -> WorkorderSyncResult:
    """
    Végrehajtja a teljes szinkronizálási folyamatot.

    1. Lekéri a CMMS asset failure cause adatokat.
    2. Elkészíti a predikció bemeneti listáját.
    3. Feloldja a belső asset_id értéket.
    4. Szinkronizálja az összes CMMS-hibaokot.
    5. Meghatározza a munkalap hibaokkapcsolatát.
    6. Elmenti vagy megkeresi a munkalapot.
    7. Elmenti a CMMS hibaokaihoz tartozó műveleteket,
       és elkészíti a predikció műveleti JSON-ját.

    A függvény nem commitol. A commit a worker
    feladata.
    """

    try:
        cmms_payload = asyncio.run(
            cmms_get_asset_failure_causes(
                workorder.cmms_asset_id
            )
        )

    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            raise DataSyncNotFoundError(
                "The CMMS returned no asset failure causes "
                f"for cmms_asset_id={workorder.cmms_asset_id}"
            ) from error

        raise

    asset_failure_cause_operations = (
        build_asset_failure_cause_operations(
            cmms_payload
        )
    )

    asset_id = ensure_asset_id(
        session=session,
        cmms_asset_id=workorder.cmms_asset_id,
    )

    failure_type_mapping = (
        synchronize_failure_causes(
            session=session,
            asset_id=asset_id,
            payload=cmms_payload,
        )
    )

    asset_failure_type_id = (
        resolve_workorder_asset_failure_type_id(
            workorder=workorder,
            failure_type_mapping=(
                failure_type_mapping
            ),
        )
    )

    worksheet = store_asset_worksheet(
        session=session,
        workorder=workorder,
        asset_id=asset_id,
        asset_failure_type_id=(
            asset_failure_type_id
        ),
    )

    cmms_operation_ids = [
        operation_id
        for failure_cause
        in asset_failure_cause_operations
        for operation_id
        in failure_cause["operation_ids"]
    ]

    store_cmms_operations(
        session=session,
        operation_ids=cmms_operation_ids,
        worksheet=worksheet,
    )

    prediction_failure_cause_operations = (
        build_prediction_failure_cause_operations(
            asset_failure_cause_operations=(
                asset_failure_cause_operations
            ),
            completed_operation_ids=workorder.operation_ids,
        )
    )

    return WorkorderSyncResult(
        asset_id=asset_id,
        asset_failure_type_id=(
            asset_failure_type_id
        ),
        asset_worksheet_list_id=int(
            worksheet.asset_worksheet_list_id
        ),
        maintenance_end_date=(
            worksheet.maintenance_end_date
        ),
        asset_failure_cause_operations=(
            prediction_failure_cause_operations
        ),
    )
