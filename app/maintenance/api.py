import logging

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_async_session
from ..dashboard.api import router as dashboard_router
from ..qc.api import router as qc_router
from .asset_mapping import apply_asset_mappings
from .jobs import enqueue_prediction_job
from .schemas import (
    AssetMappingBatchIn,
    AssetMappingBatchResult,
    AssetPredictAccepted,
    AssetPredictIn,
)
from .security import require_api_key, require_mapping_admin_api_key


log = logging.getLogger("api")


app = FastAPI(
    title="Asset Prediction API",
    version="1.0.0",
)
app.include_router(dashboard_router)
app.include_router(qc_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """
    A FastAPI alapértelmezett 422-es validációs válaszát
    400 Bad Request válasszá alakítja.
    """

    log.warning(
        "Invalid request: method=%s path=%s errors=%s",
        request.method,
        request.url.path,
        exc.errors(),
    )

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "detail": jsonable_encoder(exc.errors()),
        },
    )


@app.post(
    "/asset_predict",
    response_model=AssetPredictAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "Missing or invalid data in the request",
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "API key is missing or invalid",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The prediction job could not be stored",
        },
    },
)
async def asset_predict(
    body: AssetPredictIn,
    session: AsyncSession = Depends(get_async_session),
) -> AssetPredictAccepted:
    """
    Fogadja a CMMS munkalapadatait, és sorba állítja
    a hozzá tartozó predikciós feladatot.
    """

    try:
        job_id = await enqueue_prediction_job(
            session=session,
            body=body,
            endpoint_type="asset_predict",
        )

    except SQLAlchemyError as exc:
        await session.rollback()

        log.exception(
            "Could not enqueue prediction job: "
            "workorder_id=%s, cmms_asset_id=%s",
            body.workorder_id,
            body.cmms_asset_id,
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The prediction job could not be stored",
        ) from exc

    log.info(
        "Asset predict message accepted: "
        "job_id=%s, workorder_id=%s, cmms_asset_id=%s, prediction_queued=%s",
        job_id,
        body.workorder_id,
        body.cmms_asset_id,
        bool(
            body.operation_ids
            and (
                body.failure_date is None
                or body.failure_date <= body.ended
            )
        ),
    )

    return AssetPredictAccepted(job_id=job_id)


@app.post(
    "/sf_asset_mapping",
    response_model=AssetMappingBatchResult,
    dependencies=[Depends(require_mapping_admin_api_key)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "The mapping administration API key is invalid",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The mapping could not be stored",
        },
    },
)
async def sf_asset_mapping(
    body: AssetMappingBatchIn,
    session: AsyncSession = Depends(get_async_session),
) -> AssetMappingBatchResult:
    """Create or complete CMMS-to-DC asset mappings at runtime."""

    try:
        result = await apply_asset_mappings(session=session, batch=body)
    except SQLAlchemyError as exc:
        await session.rollback()
        log.exception("Could not apply asset mappings")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The asset mappings could not be stored",
        ) from exc

    log.info(
        "Asset mappings processed: created=%s updated=%s unchanged=%s conflicts=%s",
        result.created,
        result.updated,
        result.unchanged,
        result.conflicts,
    )
    return result
