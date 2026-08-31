from .config_log import setup_logging

import asyncio
import time
from datetime import datetime

from loguru import logger
from pydantic import ValidationError
from sqlalchemy.orm import Session
from sqlalchemy import select

from .data_sync import (DataSyncNotFoundError, DataSyncValidationError, synchronize_workorder)
from ..models import (AssetFailureType, JobStatus, Prediction, PredictionJob)
from .schemas import (AssetFailureCausePredictionPayload, AssetPredictionPayload, AssetPredictIn, FailureCausePredictionItem)
from .cmms import (cmms_post_asset_failure_cause_prediction, cmms_post_asset_prediction)
from .job_queue import (_is_admin_shutdown_error, claim_one_job, job_heartbeat, requeue_stuck_jobs, session_scope)

# !!Dummy predikció cmms-dt kapcsolat teszteléséhez
# from .test_predict import predict

# !!Predikciós modul importálása
from .prediction_config import prediction_config
from prediction_module.core import run_prediction

setup_logging()


POLL_INTERVAL_SEC = 1.0
REQUEUE_INTERVAL_SEC = 30.0


def update_job_status(session: Session, job_id: int, status: JobStatus, error_message: str | None = None) -> None:
    """
    Frissíti egy prediction_jobs rekord állapotát.
    """

    job = session.get(PredictionJob, int(job_id))

    if job is None:
        return

    job.status = status
    job.error_message = error_message
    job.updated_at = datetime.utcnow()

    session.commit()


def normalize_probability(value: object, field_name: str) -> float:
    """
    Egy valószínűségi értéket float típusra alakít,
    majd ellenőrzi, hogy 0 és 1 közé esik-e.
    """

    try:
        probability = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{field_name} must be numeric"
        ) from error

    if not 0.0 <= probability <= 1.0:
        raise ValueError(
            f"{field_name} must be between 0 and 1"
        )

    return probability


def validate_prediction_result(prediction_result: object) -> tuple[int, list[int], list[float], float]:
    """
    Ellenőrzi a predikciós modul kimenetét.

    Elvárt kimenet:

        {
            "prediction_id": 1,
            "failure_type_ids": [4, 7],
            "failure_type_probability": [0.12, 0.23],
            "predicted_reliability": 0.894
        }
    """

    if not isinstance(prediction_result, dict):
        raise ValueError("predict() must return a dictionary")

    prediction_id_raw = prediction_result.get("prediction_id")

    if prediction_id_raw is None:
        raise ValueError("prediction_id is missing from the prediction result")

    prediction_id = int(prediction_id_raw)

    if prediction_id <= 0:
        raise ValueError("prediction_id must be greater than zero")

    failure_type_ids_raw = prediction_result.get("failure_type_ids")

    if not isinstance(failure_type_ids_raw, list):
        raise ValueError("failure_type_ids must be a list")

    failure_type_probability_raw = (prediction_result.get("failure_type_probability"))

    if not isinstance(failure_type_probability_raw, list):
        raise ValueError("failure_type_probability must be a list")

    if (len(failure_type_ids_raw) != len(failure_type_probability_raw)):
        raise ValueError("failure_type_ids and failure_type_probability must have the same number of elements")

    if not failure_type_ids_raw:
        raise ValueError("The prediction returned no failure-cause results")

    failure_type_ids: list[int] = []

    for failure_type_id_raw in (failure_type_ids_raw):
        failure_type_id = int(failure_type_id_raw)

        if failure_type_id <= 0:
            raise ValueError("Every failure_type_id must be greater than zero")

        failure_type_ids.append(failure_type_id)

    if (len(failure_type_ids) != len(set(failure_type_ids))):
        raise ValueError("failure_type_ids contains duplicates")

    failure_type_probabilities: list[float] = []

    for index, probability_raw in enumerate(failure_type_probability_raw):
        probability = normalize_probability(probability_raw, f"failure_type_probability[{index}]")

        failure_type_probabilities.append(probability)

    predicted_reliability = (normalize_probability(prediction_result.get("predicted_reliability"), "predicted_reliability"))

    return (prediction_id, failure_type_ids, failure_type_probabilities, predicted_reliability)


def verify_stored_prediction(session: Session, prediction_id: int, job_id: int, asset_id: int) -> Prediction:
    """
    Ellenőrzi, hogy a predikciós modul valóban
    elmentette-e a visszaadott prediction_id rekordját.

    Ellenőrzi azt is, hogy a rekord a megfelelő
    jobhoz és assethez tartozik-e.
    """

    session.expire_all()

    stored_prediction = session.get(Prediction, int(prediction_id))

    if stored_prediction is None:
        raise ValueError("The prediction module returned " f"prediction_id={prediction_id}, but this prediction was not stored in the predictions table")

    if (int(stored_prediction.job_id) != int(job_id)):
        raise ValueError("The stored prediction belongs to another job")

    if (int(stored_prediction.asset_id) != int(asset_id)):
        raise ValueError("The stored prediction belongs to another asset")

    return stored_prediction


def build_failure_cause_items(asset_failurecause_ids: list[int], failure_type_probabilities: list[float]) -> list[FailureCausePredictionItem]:
    """
    A feloldott asset_failurecause_id értékeket
    összepárosítja a predikció valószínűségeivel.
    """

    if (len(asset_failurecause_ids) != len(failure_type_probabilities)):
        raise ValueError("asset_failurecause_ids and failure_type_probabilities must have the same number of elements")

    return [FailureCausePredictionItem(asset_failurecause_id=(asset_failurecause_id), predicted_occurrence_probability=probability)
            for (asset_failurecause_id, probability) in zip(asset_failurecause_ids, failure_type_probabilities)]


def resolve_asset_failurecause_ids(session: Session, asset_id: int, failure_type_ids: list[int]) -> list[int]:
    """
    A predikcióból kapott failure_type_id értékeket
    az adott belső asset_id alapján feloldja a CMMS
    asset_failurecause_id értékekre.

    A visszatérési lista sorrendje megegyezik a
    failure_type_ids lista sorrendjével.
    """

    if not failure_type_ids:
        raise ValueError("failure_type_ids cannot be empty")

    rows = session.execute(
        select(
            AssetFailureType.failure_type_id,
            AssetFailureType.asset_failurecause_id,
        ).where(
            AssetFailureType.asset_id == int(asset_id),

            AssetFailureType.failure_type_id.in_(
                failure_type_ids
            ),
        )
    ).all()

    failure_type_mapping: dict[int, int] = {}

    for (failure_type_id, asset_failurecause_id) in rows:
        if failure_type_id is None:
            continue

        normalized_failure_type_id = int(failure_type_id)

        if asset_failurecause_id is None:
            raise ValueError("asset_failurecause_id is NULL for " f"asset_id={asset_id}, " "failure_type_id=" f"{normalized_failure_type_id}")

        normalized_asset_failurecause_id = int(asset_failurecause_id)

        if (normalized_failure_type_id in failure_type_mapping):
            raise ValueError("Multiple asset_failure_types rows were found for " f"asset_id={asset_id}, " "failure_type_id=" f"{normalized_failure_type_id}")

        failure_type_mapping[normalized_failure_type_id] = normalized_asset_failurecause_id

    missing_failure_type_ids = [failure_type_id for failure_type_id in failure_type_ids if failure_type_id not in failure_type_mapping]

    if missing_failure_type_ids:
        raise ValueError("No asset_failurecause_id was found " f"for asset_id={asset_id} and " "failure_type_ids=" f"{missing_failure_type_ids}")

    return [failure_type_mapping[failure_type_id] for failure_type_id in failure_type_ids]


def process_job(session: Session, job: PredictionJob) -> None:
    """
    Feldolgoz egy már lefoglalt prediction jobot.
    """

    job_id = int(job.job_id)

    if job.endpoint_type != "asset_predict":
        update_job_status(session=session, job_id=job_id, status=JobStatus.error, error_message=("Unsupported endpoint_type: " f"{job.endpoint_type}"))
        return

    try:
        workorder = AssetPredictIn.model_validate(job.payload)

    except ValidationError as error:
        update_job_status(session=session, job_id=job_id, status=JobStatus.error, error_message=("Payload validation failed: " f"{error}"))
        return

    try:
        sync_result = synchronize_workorder(session=session, workorder=workorder)

        # A karbantartási adatok mentése:
        #
        # - failure_types
        # - asset_failure_types
        # - asset_worksheet_lists
        # - operations_done_lists
        #
        # A commit a predikció előtt történik, mert
        # a predikciós modul saját DB-sessiont használhat.
        session.commit()

    except DataSyncNotFoundError as error:
        session.rollback()

        update_job_status(session=session, job_id=job_id, status=JobStatus.not_found, error_message=str(error))
        return

    except (DataSyncValidationError, ValueError, TypeError) as error:
        session.rollback()

        update_job_status(session=session, job_id=job_id, status=JobStatus.error, error_message=("Workorder synchronization failed: " f"{error}"))
        return

    if not workorder.operation_ids:
        update_job_status(
            session=session,
            job_id=job_id,
            status=JobStatus.skipped,
            error_message=(
                "Prediction skipped: operation_ids is missing or empty"
            ),
        )

        logger.info(
            "Prediction skipped after CMMS synchronization: job_id={}, reason={}",
            job_id,
            "operation_ids is missing or empty",
        )
        return

    try:
        # A predikciós modul pontosan ezt az öt
        # előre meghatározott bemenetet kapja.
        #
        # A modul feladata:
        #
        # 1. a predikció kiszámítása;
        # 2. a predikciós táblák feltöltése;
        # 3. az adatbázis-commit;
        # 4. az eredmény visszaadása.

        # !! dummy predikció teszt, predikciós modul nélkül
        # prediction_result = predict(job_id=job_id, maintenance_end_time=(workorder.ended), failure_start_time=(workorder.failure_date),
        #                            asset_id=(sync_result.asset_id), asset_failure_cause_operations=(sync_result.asset_failure_cause_operations),
        #                            delta_sampling=prediction_config.delta_sampling, delta_horizon=prediction_config.delta_horizon)

        # !! predikciós modul meghívása
        prediction_result = run_prediction(asset_id=sync_result.asset_id, job_id=job_id, operation_template_dict=sync_result.asset_failure_cause_operations, failure_start_time=workorder.failure_date,
                                           maintenance_end_time=workorder.ended, delta_horizon=prediction_config.delta_horizon, delta_sampling=prediction_config.delta_sampling, session=session)

        (prediction_id, failure_type_ids, failure_type_probabilities, predicted_reliability) = validate_prediction_result(prediction_result=prediction_result)

        asset_failurecause_ids = (resolve_asset_failurecause_ids(session=session, asset_id=sync_result.asset_id, failure_type_ids=failure_type_ids))

        # A worker nem ír a predictions táblába.
        # Csak ellenőrzi, hogy a predikciós modul
        # elmentette-e a visszaadott eredményt.
        verify_stored_prediction(session=session, prediction_id=prediction_id, job_id=job_id, asset_id=(sync_result.asset_id))

    except Exception as error:
        if _is_admin_shutdown_error(error):
            raise

        update_job_status(session=session, job_id=job_id, status=JobStatus.error, error_message=("Prediction failed: " f"{error}"))
        return

    try:
        asset_prediction_payload = (AssetPredictionPayload(prediction_id=prediction_id, cmms_asset_id=(workorder.cmms_asset_id), predicted_reliability=(predicted_reliability)))

        failure_cause_payload = (AssetFailureCausePredictionPayload(prediction_id=prediction_id,
                                                                    failure_causes=(build_failure_cause_items(asset_failurecause_ids=(asset_failurecause_ids),
                                                                                                              failure_type_probabilities=(failure_type_probabilities)))))

        asset_response = asyncio.run(cmms_post_asset_prediction(asset_prediction_payload.model_dump(mode="json", by_alias=True)))

        logger.info("CMMS asset prediction response: {}", asset_response)

        failure_cause_response = asyncio.run(cmms_post_asset_failure_cause_prediction(failure_cause_payload.model_dump(mode="json")))

        logger.info("CMMS failure-cause prediction " "response: {}", failure_cause_response)

    except Exception as error:
        update_job_status(session=session, job_id=job_id, status=JobStatus.error, error_message=("CMMS prediction POST failed: " f"{error}"))
        return

    update_job_status(session=session, job_id=job_id, status=JobStatus.done, error_message=None)

    logger.info("Prediction job completed: " "job_id={}, prediction_id={}", job_id, prediction_id)


def main() -> None:
    logger.info("DB queue worker started.")

    last_requeue = 0.0

    while True:
        try:
            now = time.monotonic()

            if (now - last_requeue >= REQUEUE_INTERVAL_SEC):
                with session_scope() as session:
                    requeue_stuck_jobs(session)

                last_requeue = now

            # A következő queued job lefoglalása
            # egy rövid adatbázis-sessionben.
            with session_scope() as session:
                claimed_job = claim_one_job(session)

                job_id = (int(claimed_job.job_id) if claimed_job is not None else None)

            if job_id is None:
                time.sleep(POLL_INTERVAL_SEC)
                continue

            # A tényleges feldolgozás külön
            # adatbázis-sessionben történik.
            with session_scope() as session:
                job = session.get(PredictionJob, job_id)

                if job is None:
                    continue

                try:
                    with job_heartbeat(job_id):
                        process_job(session=session, job=job)

                except Exception as error:
                    logger.exception("Unhandled process-job error: {}", error)

                    # Egy hibás tranzakció után új
                    # sessionben állítjuk vissza a jobot.
                    with session_scope() as (recovery_session):
                        recovery_job = (recovery_session.get(PredictionJob, job_id))

                        if recovery_job is None:
                            continue

                        if _is_admin_shutdown_error(error):
                            recovery_job.status = (JobStatus.queued)
                            recovery_job.error_message = ("Retry: database connection terminated")
                        else:
                            recovery_job.status = (JobStatus.error)
                            recovery_job.error_message = ("Unhandled error: " f"{error}")

                        recovery_job.updated_at = (datetime.utcnow())

        except Exception as error:
            logger.exception("Worker loop error: {}", error)

            time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
