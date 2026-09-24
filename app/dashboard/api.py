import asyncio
import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal, get_async_session
from ..maintenance.security import require_api_key
from ..models import UserType
from ..settings import settings
from .schemas import (
    AssetOut,
    BaseMetric,
    ChartRequest,
    Graph,
    LoginRequest,
    OptionItem,
    OrderCreate,
    OrderListItem,
    OrderOut,
    ProcessStepProducts,
    ProductOut,
    SensorOut,
    StationTrackingEventRequest,
    TrackingEventRequest,
    TrackingEventResult,
    TrayAssignmentRequest,
    TrayAssignmentResult,
)
from .service import (
    add_station_tracking_event,
    add_tracking_event,
    assign_tray_to_next_product,
    cancel_order,
    complete_order,
    create_order,
    get_chart_data,
    get_order,
    get_process_assets,
    get_process_graph,
    get_process_product_locations,
    get_sensor_details,
    list_kpis,
    list_orders,
    list_products,
)


router = APIRouter(tags=["Dashboard"])


@router.post("/auth/login", response_model=bool, tags=["User"])
async def login(_: LoginRequest) -> bool:
    """Authentication is intentionally deferred for the first dashboard release."""

    return True


@router.get("/user-types", response_model=list[OptionItem], tags=["User"])
async def user_types(
    session: AsyncSession = Depends(get_async_session),
) -> list[OptionItem]:
    rows = (
        await session.execute(select(UserType).order_by(UserType.user_type_id))
    ).scalars()
    return [OptionItem(id=row.user_type_id, name=row.user_type_name) for row in rows]


@router.get("/process/graph", response_model=Graph, tags=["Process"])
async def process_graph(
    session: AsyncSession = Depends(get_async_session),
) -> Graph:
    return await get_process_graph(session)


@router.get(
    "/process/products",
    response_model=list[ProcessStepProducts],
    tags=["Production tracking"],
)
async def process_products(
    session: AsyncSession = Depends(get_async_session),
) -> list[ProcessStepProducts]:
    return await get_process_product_locations(session)


@router.get("/process/{process_id}", response_model=list[AssetOut], tags=["Process"])
async def process_details(
    process_id: int,
    source: str = "real",
    session: AsyncSession = Depends(get_async_session),
) -> list[AssetOut]:
    return await get_process_assets(session, process_id, source)


@router.post(
    "/sensors/chart",
    response_model=list[dict[str, float | str]],
    tags=["Process"],
)
async def sensor_chart(
    body: ChartRequest,
    session: AsyncSession = Depends(get_async_session),
) -> list[dict[str, float | str]]:
    return await get_chart_data(session, body)


@router.get("/sensors", response_model=list[SensorOut], tags=["Process"])
async def sensors(
    sensor_ids: list[int] = Query(alias="sensorIDs"),
    source: str = "real",
    session: AsyncSession = Depends(get_async_session),
) -> list[SensorOut]:
    return await get_sensor_details(session, sensor_ids, source)


@router.get("/products", response_model=list[ProductOut], tags=["Products"])
async def products(
    session: AsyncSession = Depends(get_async_session),
) -> list[ProductOut]:
    return await list_products(session)


@router.get("/orders", response_model=list[OrderListItem], tags=["Orders"])
async def orders(
    session: AsyncSession = Depends(get_async_session),
) -> list[OrderListItem]:
    return await list_orders(session, completed=False)


@router.post("/orders/add", response_model=bool, tags=["Orders"])
async def add_order(
    body: OrderCreate,
    session: AsyncSession = Depends(get_async_session),
) -> bool:
    return await create_order(session, body)


@router.get(
    "/orders/completed", response_model=list[OrderListItem], tags=["Orders"]
)
async def completed_orders(
    session: AsyncSession = Depends(get_async_session),
) -> list[OrderListItem]:
    return await list_orders(session, completed=True)


@router.get("/orders/current", response_model=OrderOut, tags=["Orders"])
async def current_order(
    session: AsyncSession = Depends(get_async_session),
) -> OrderOut:
    return await get_order(session)


@router.get("/orders/{order_id}", response_model=OrderOut, tags=["Orders"])
async def order_details(
    order_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> OrderOut:
    return await get_order(session, order_id)


@router.delete("/orders/{order_id}/delete", response_model=bool, tags=["Orders"])
async def delete_order(
    order_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> bool:
    return await cancel_order(session, order_id)


@router.post("/orders/{order_id}/complete", response_model=bool, tags=["Orders"])
async def finish_order(
    order_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> bool:
    return await complete_order(session, order_id)


@router.get("/kpis/{user_id}", response_model=list[BaseMetric], tags=["User"])
async def kpis(
    user_id: int,
    source: str = "real",
    session: AsyncSession = Depends(get_async_session),
) -> list[BaseMetric]:
    return await list_kpis(session, user_id, source)


@router.post(
    "/trays/{nfc_tag_id}/assign-next",
    response_model=TrayAssignmentResult,
    tags=["Production tracking"],
)
async def assign_tray(
    nfc_tag_id: str,
    body: TrayAssignmentRequest,
    session: AsyncSession = Depends(get_async_session),
) -> TrayAssignmentResult:
    return await assign_tray_to_next_product(session, nfc_tag_id, body.orderId)


@router.post(
    "/products/{product_instance_id}/events",
    response_model=TrackingEventResult,
    tags=["Production tracking"],
)
async def track_product(
    product_instance_id: int,
    body: TrackingEventRequest,
    session: AsyncSession = Depends(get_async_session),
) -> TrackingEventResult:
    return await add_tracking_event(session, product_instance_id, body)


@router.post(
    "/production/events",
    response_model=TrackingEventResult,
    tags=["Production tracking"],
    dependencies=[Depends(require_api_key)],
)
async def station_tracking_event(
    body: StationTrackingEventRequest,
    session: AsyncSession = Depends(get_async_session),
) -> TrackingEventResult:
    return await add_station_tracking_event(session, body)


@router.websocket("/ws/process/products")
async def process_products_websocket(websocket: WebSocket) -> None:
    """Stream complete process-step product occupancy snapshots."""

    await websocket.accept()
    previous: list[dict[str, Any]] | None = None
    try:
        while True:
            async with AsyncSessionLocal() as session:
                snapshot = await get_process_product_locations(session)
            payload = [item.model_dump() for item in snapshot]
            if payload != previous:
                await websocket.send_json(payload)
                previous = payload
            try:
                message = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=settings.DASHBOARD_WS_POLL_INTERVAL_SECONDS,
                )
                if message["type"] == "websocket.disconnect":
                    return
            except TimeoutError:
                pass
    except WebSocketDisconnect:
        return


def _bucket_start(value: datetime, frequency: float) -> datetime:
    epoch = datetime(1970, 1, 1)
    seconds = (value - epoch).total_seconds()
    return epoch + timedelta(seconds=math.floor(seconds / frequency) * frequency)


def _aggregate_value(state: dict[str, Any], method: str) -> float:
    if method == "average":
        return float(state["sum"] / state["count"])
    if method == "minimum":
        return float(state["minimum"])
    if method == "maximum":
        return float(state["maximum"])
    return float(state["latest"])


@router.websocket("/ws/sensors/chart")
async def sensor_chart_websocket(websocket: WebSocket) -> None:
    """Stream new chart points while keeping the REST ChartData shape."""

    await websocket.accept()
    try:
        try:
            request = ChartRequest.model_validate(await websocket.receive_json())
        except ValidationError as exc:
            await websocket.send_json({"detail": exc.errors()})
            await websocket.close(code=1008, reason="Invalid subscription")
            return

        async with AsyncSessionLocal() as session:
            config_statement = text(
                "SELECT s.sensor_id, "
                "GREATEST(:requested_frequency, "
                "COALESCE(NULLIF(s.measurement_frequency, 0), :requested_frequency)) "
                "AS frequency, s.chart_aggregation_method "
                "FROM sensors s WHERE s.sensor_id IN :sensor_ids"
            ).bindparams(bindparam("sensor_ids", expanding=True))
            configs = (
                await session.execute(
                    config_statement,
                    {
                        "requested_frequency": request.filter.samplingFrequency,
                        "sensor_ids": request.sensorIds,
                    },
                )
            ).mappings().all()
            sensor_config = {
                int(row["sensor_id"]): (
                    float(row["frequency"]),
                    str(row["chart_aggregation_method"]),
                )
                for row in configs
            }
            initial_cursor_statement = text(
                "SELECT COALESCE(max(m.measurement_id), 0) FROM measurements m "
                "JOIN data_sources ds ON ds.data_source_id = m.data_source_id "
                "WHERE m.sensor_id IN :sensor_ids AND ds.source_key = :source "
                "AND m.time >= :from_date AND m.time <= :to_date"
            ).bindparams(bindparam("sensor_ids", expanding=True))
            last_measurement_id = int(
                await session.scalar(
                    initial_cursor_statement,
                    {
                        "sensor_ids": list(sensor_config),
                        "source": request.source,
                        "to_date": request.filter.toDate,
                        "from_date": request.filter.fromDate,
                    },
                )
                or 0
            )

            hydrate_statement = text(
                "WITH config AS (SELECT s.sensor_id, "
                "GREATEST(:requested_frequency, "
                "COALESCE(NULLIF(s.measurement_frequency, 0), :requested_frequency)) "
                "AS frequency FROM sensors s WHERE s.sensor_id IN :sensor_ids), "
                "bucket_config AS (SELECT sensor_id, frequency, "
                "timestamp 'epoch' + floor(extract(epoch FROM "
                "(:to_date - timestamp 'epoch')) / frequency) * frequency "
                "* interval '1 second' AS bucket FROM config) "
                "SELECT bc.sensor_id, bc.bucket, sum(m.value) AS value_sum, "
                "count(*) AS value_count, min(m.value) AS minimum, "
                "max(m.value) AS maximum, last(m.value, m.time) AS latest, "
                "max(m.time) AS latest_time, max(m.measurement_id) AS latest_id "
                "FROM bucket_config bc JOIN measurements m "
                "ON m.sensor_id = bc.sensor_id AND m.time >= bc.bucket "
                "AND m.time <= :to_date JOIN data_sources ds "
                "ON ds.data_source_id = m.data_source_id AND ds.source_key = :source "
                "GROUP BY bc.sensor_id, bc.bucket"
            ).bindparams(bindparam("sensor_ids", expanding=True))
            hydrated_rows = (
                await session.execute(
                    hydrate_statement,
                    {
                        "requested_frequency": request.filter.samplingFrequency,
                        "sensor_ids": list(sensor_config),
                        "source": request.source,
                        "to_date": request.filter.toDate,
                    },
                )
            ).mappings().all()

        if not sensor_config:
            await websocket.close(code=1008, reason="No valid sensors")
            return

        states: dict[tuple[int, datetime], dict[str, Any]] = {
            (int(row["sensor_id"]), row["bucket"]): {
                "sum": float(row["value_sum"]),
                "count": int(row["value_count"]),
                "minimum": float(row["minimum"]),
                "maximum": float(row["maximum"]),
                "latest": float(row["latest"]),
                "latest_time": row["latest_time"],
                "latest_id": int(row["latest_id"]),
            }
            for row in hydrated_rows
        }
        live_scan_start = request.filter.toDate - timedelta(
            seconds=max(frequency for frequency, _ in sensor_config.values())
        )
        measurement_statement = text(
            "SELECT m.measurement_id, m.sensor_id, m.time, m.value "
            "FROM measurements m JOIN data_sources ds "
            "ON ds.data_source_id = m.data_source_id "
            "WHERE m.measurement_id > :last_id AND m.sensor_id IN :sensor_ids "
            "AND ds.source_key = :source "
            "AND m.time >= :scan_from_time "
            "ORDER BY m.measurement_id"
        ).bindparams(bindparam("sensor_ids", expanding=True))

        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=settings.DASHBOARD_WS_POLL_INTERVAL_SECONDS,
                )
                if message["type"] == "websocket.disconnect":
                    return
            except TimeoutError:
                pass
            async with AsyncSessionLocal() as session:
                rows = (
                    await session.execute(
                        measurement_statement,
                        {
                            "last_id": last_measurement_id,
                            "sensor_ids": list(sensor_config),
                            "source": request.source,
                            "scan_from_time": live_scan_start,
                        },
                    )
                ).mappings().all()
            if not rows:
                continue

            changed_buckets: set[datetime] = set()
            for row in rows:
                measurement_id = int(row["measurement_id"])
                last_measurement_id = max(last_measurement_id, measurement_id)
                sensor_id = int(row["sensor_id"])
                frequency, _ = sensor_config[sensor_id]
                bucket = _bucket_start(row["time"], frequency)
                key = (sensor_id, bucket)
                value = float(row["value"])
                state = states.setdefault(
                    key,
                    {
                        "sum": 0.0,
                        "count": 0,
                        "minimum": value,
                        "maximum": value,
                        "latest": value,
                        "latest_time": row["time"],
                        "latest_id": measurement_id,
                    },
                )
                state["sum"] += value
                state["count"] += 1
                state["minimum"] = min(state["minimum"], value)
                state["maximum"] = max(state["maximum"], value)
                if (row["time"], measurement_id) >= (
                    state["latest_time"],
                    state["latest_id"],
                ):
                    state["latest"] = value
                    state["latest_time"] = row["time"]
                    state["latest_id"] = measurement_id
                changed_buckets.add(bucket)

            points: dict[datetime, dict[str, float | str]] = defaultdict(dict)
            for (sensor_id, bucket), state in states.items():
                if bucket not in changed_buckets:
                    continue
                _, method = sensor_config[sensor_id]
                points[bucket][str(sensor_id)] = _aggregate_value(state, method)
            payload = []
            for bucket in sorted(points):
                payload.append(
                    {
                        "xAxis": bucket.isoformat(timespec="milliseconds"),
                        **points[bucket],
                    }
                )
            if payload:
                await websocket.send_json(payload)

            cutoff = datetime.now() - timedelta(
                seconds=max(frequency for frequency, _ in sensor_config.values()) * 2
            )
            states = {
                key: state for key, state in states.items() if key[1] >= cutoff
            }
    except WebSocketDisconnect:
        return
