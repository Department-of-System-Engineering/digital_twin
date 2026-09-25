from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import bindparam, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Order,
    OrderItem,
    ProductInstance,
    ProductTrackingEvent,
    ProductType,
    Tray,
    TrayProductAssignment,
)
from ..settings import settings
from .schemas import (
    AssetOut,
    BaseMetric,
    ChartRequest,
    Graph,
    GraphEdge,
    GraphNode,
    OrderCreate,
    OrderListItem,
    OrderOut,
    ProcessStepProducts,
    ProductOut,
    SensorOut,
    StationTrackingEventRequest,
    TrackingEventRequest,
    TrackingEventResult,
    TrayAssignmentResult,
    TrackedProduct,
)


def _number_type(type_name: str | None, unit: str | None) -> str:
    if unit == "%":
        return "percent"
    normalized = (type_name or "").lower()
    if "int" in normalized or "long" in normalized:
        return "int"
    return "float"


def _date_string(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


async def get_process_graph(session: AsyncSession) -> Graph:
    configuration_id = await session.scalar(
        text(
            "SELECT process_configuration_id FROM process_configurations "
            "WHERE is_active ORDER BY valid_from DESC LIMIT 1"
        )
    )
    if configuration_id is None:
        return Graph(nodes=[], edges=[])

    node_rows = (
        await session.execute(
            text(
                "SELECT ps.process_step_id, ps.process_step_name, "
                "min(a.asset_key) AS asset_key, "
                "string_agg(DISTINCT COALESCE(a.asset_name, 'Asset ' || a.asset_id::text), "
                "', ' ORDER BY COALESCE(a.asset_name, 'Asset ' || a.asset_id::text)) AS assets "
                "FROM process_steps ps "
                "LEFT JOIN asset_process_steps aps "
                "ON aps.process_step_id = ps.process_step_id "
                "AND aps.process_configuration_id = :configuration_id "
                "LEFT JOIN assets a ON a.asset_id = aps.asset_id "
                "WHERE aps.asset_process_step_id IS NOT NULL "
                "OR EXISTS (SELECT 1 FROM routing r WHERE "
                "r.process_configuration_id = :configuration_id AND "
                "(r.process_step_id = ps.process_step_id "
                "OR r.next_process_step_id = ps.process_step_id)) "
                "GROUP BY ps.process_step_id, ps.process_step_name "
                "ORDER BY ps.process_step_id"
            ),
            {"configuration_id": configuration_id},
        )
    ).mappings()

    edge_rows = (
        await session.execute(
            text(
                "SELECT routing_id, process_step_id, next_process_step_id "
                "FROM routing WHERE process_configuration_id = :configuration_id "
                "ORDER BY routing_id"
            ),
            {"configuration_id": configuration_id},
        )
    ).mappings()

    node_rows = list(node_rows)
    preparation_ids = [
        int(row["process_step_id"])
        for row in node_rows
        if row["asset_key"] == "preparation"
    ]
    preparation_node_id = min(preparation_ids) if preparation_ids else None
    aliases = {
        int(row["process_step_id"]): (
            preparation_node_id
            if row["asset_key"] == "preparation" and preparation_node_id is not None
            else int(row["process_step_id"])
        )
        for row in node_rows
    }

    nodes = []
    emitted_node_ids: set[int] = set()
    station_names = {
        "assembly1": "Assembly 1 Station",
        "assembly2": "Assembly 2 Station",
        "visual_qc": "Visual QC Station",
    }
    for row in node_rows:
        node_id = aliases[int(row["process_step_id"])]
        if node_id in emitted_node_ids:
            continue
        emitted_node_ids.add(node_id)
        name = str(row["process_step_name"])
        if row["asset_key"] == "preparation":
            name = "Base preparation - Preparation Station"
        elif row["asset_key"] in station_names:
            name = f"{name} - {station_names[row['asset_key']]}"
        elif row["assets"]:
            name = f"{name} - {row['assets']}"
        nodes.append(GraphNode(id=str(node_id), name=name))

    edges = []
    emitted_edges: set[tuple[int, int]] = set()
    for row in edge_rows:
        source = aliases[int(row["process_step_id"])]
        target = aliases[int(row["next_process_step_id"])]
        if source == target or (source, target) in emitted_edges:
            continue
        emitted_edges.add((source, target))
        edges.append(GraphEdge(id=f"{source}-{target}", source=str(source), target=str(target)))
    return Graph(nodes=nodes, edges=edges)


async def get_process_product_locations(
    session: AsyncSession,
) -> list[ProcessStepProducts]:
    rows = (
        await session.execute(
            text(
                "WITH active_configuration AS ("
                "SELECT process_configuration_id FROM process_configurations "
                "WHERE is_active ORDER BY valid_from DESC LIMIT 1), "
                "latest_event AS ("
                "SELECT DISTINCT ON (pte.product_instance_id) "
                "pte.product_instance_id, pte.process_step_id, "
                "pte.next_process_step_id, pte.state "
                "FROM product_tracking_events pte "
                "ORDER BY pte.product_instance_id, pte.time DESC, "
                "pte.product_tracking_event_id DESC), "
                "route_choice AS ("
                "SELECT r.process_step_id, count(*) AS route_count, "
                "min(r.next_process_step_id) AS only_next_process_step_id "
                "FROM routing r JOIN active_configuration ac "
                "ON ac.process_configuration_id = r.process_configuration_id "
                "GROUP BY r.process_step_id), "
                "located AS ("
                "SELECT le.product_instance_id, CASE WHEN le.state = 'departed' "
                "THEN COALESCE(le.next_process_step_id, CASE "
                "WHEN rc.route_count = 1 THEN rc.only_next_process_step_id END) "
                "ELSE le.process_step_id END AS step_id "
                "FROM latest_event le LEFT JOIN route_choice rc "
                "ON rc.process_step_id = le.process_step_id), "
                "display_alias AS ("
                "SELECT aps.process_step_id, CASE WHEN a.asset_key = 'preparation' "
                "THEN min(aps.process_step_id) OVER (PARTITION BY aps.process_configuration_id, aps.asset_id) "
                "ELSE aps.process_step_id END AS display_step_id "
                "FROM asset_process_steps aps JOIN assets a ON a.asset_id = aps.asset_id "
                "JOIN active_configuration ac ON ac.process_configuration_id = aps.process_configuration_id) "
                "SELECT COALESCE(da.display_step_id, l.step_id) AS process_step_id, "
                "pi.product_instance_id, pt.product_type_name "
                "FROM located l JOIN product_instances pi ON pi.product_instance_id = l.product_instance_id "
                "JOIN order_items oi ON oi.order_item_id = pi.order_item_id "
                "JOIN orders o ON o.order_id = oi.order_id "
                "JOIN product_types pt ON pt.product_type_id = oi.product_type_id "
                "LEFT JOIN display_alias da ON da.process_step_id = l.step_id "
                "WHERE l.step_id IS NOT NULL AND o.status IN ('pending', 'in_progress') "
                "ORDER BY process_step_id, pi.product_instance_id"
            )
        )
    ).mappings()
    grouped: dict[str, list[TrackedProduct]] = defaultdict(list)
    for row in rows:
        grouped[str(row["process_step_id"])].append(
            TrackedProduct(
                productInstanceId=int(row["product_instance_id"]),
                productType=str(row["product_type_name"]),
            )
        )
    return [
        ProcessStepProducts(processStepId=step_id, products=products)
        for step_id, products in grouped.items()
    ]


_SENSOR_DETAILS_SQL = text(
    "SELECT DISTINCT a.asset_id, COALESCE(a.asset_name, 'Asset ' || a.asset_id::text) "
    "AS asset_name, s.sensor_id, s.sensor_name, st.type_name, st.min_value, "
    "COALESCE(mt.unit_symbol, mt.unit, '') AS unit, "
    "(SELECT m.value FROM measurements m JOIN data_sources ds "
    "ON ds.data_source_id = m.data_source_id "
    "WHERE m.sensor_id = s.sensor_id AND ds.source_key = :source "
    "ORDER BY m.time DESC, m.measurement_id DESC LIMIT 1) AS latest_value "
    "FROM asset_process_steps aps "
    "JOIN process_configurations pc "
    "ON pc.process_configuration_id = aps.process_configuration_id AND pc.is_active "
    "JOIN assets a ON a.asset_id = aps.asset_id "
    "LEFT JOIN sensors s ON s.asset_id = a.asset_id "
    "LEFT JOIN sensor_types st ON st.type_id = s.type_id "
    "LEFT JOIN measurement_types mt ON mt.measurement_type_id = st.measurement_type_id "
    "WHERE aps.process_step_id = :process_step_id "
    "ORDER BY a.asset_id, s.sensor_id"
)


async def get_process_assets(
    session: AsyncSession, process_step_id: int, source: str = "real"
) -> list[AssetOut]:
    rows = (
        await session.execute(
            _SENSOR_DETAILS_SQL,
            {"process_step_id": process_step_id, "source": source},
        )
    ).mappings()
    assets: dict[int, AssetOut] = {}
    for row in rows:
        asset_id = int(row["asset_id"])
        asset = assets.setdefault(
            asset_id,
            AssetOut(assetID=asset_id, assetName=str(row["asset_name"]), sensors=[]),
        )
        if row["sensor_id"] is not None:
            asset.sensors.append(
                SensorOut(
                    id=int(row["sensor_id"]),
                    name=str(row["sensor_name"]),
                    unit=str(row["unit"]),
                    type=_number_type(row["type_name"], row["unit"]),
                    value=float(row["latest_value"])
                    if row["latest_value"] is not None
                    else None,
                    min=float(row["min_value"])
                    if row["min_value"] is not None
                    else None,
                )
            )
    return list(assets.values())


async def get_sensor_details(
    session: AsyncSession, sensor_ids: list[int], source: str = "real"
) -> list[SensorOut]:
    if not sensor_ids:
        return []
    statement = text(
        "SELECT s.sensor_id, s.sensor_name, st.type_name, st.min_value, "
        "COALESCE(mt.unit_symbol, mt.unit, '') AS unit, "
        "(SELECT m.value FROM measurements m JOIN data_sources ds "
        "ON ds.data_source_id = m.data_source_id "
        "WHERE m.sensor_id = s.sensor_id AND ds.source_key = :source "
        "ORDER BY m.time DESC, m.measurement_id DESC LIMIT 1) AS latest_value "
        "FROM sensors s JOIN sensor_types st ON st.type_id = s.type_id "
        "JOIN measurement_types mt ON mt.measurement_type_id = st.measurement_type_id "
        "WHERE s.sensor_id IN :sensor_ids ORDER BY s.sensor_id"
    ).bindparams(bindparam("sensor_ids", expanding=True))
    rows = (
        await session.execute(
            statement, {"sensor_ids": sensor_ids, "source": source}
        )
    ).mappings()
    return [
        SensorOut(
            id=int(row["sensor_id"]),
            name=str(row["sensor_name"]),
            unit=str(row["unit"]),
            type=_number_type(row["type_name"], row["unit"]),
            value=float(row["latest_value"])
            if row["latest_value"] is not None
            else None,
            min=float(row["min_value"]) if row["min_value"] is not None else None,
        )
        for row in rows
    ]


def _chart_query_start(request: ChartRequest) -> datetime:
    """Limit history work to the number of points the dashboard can display."""

    visible_window = timedelta(
        seconds=(
            request.filter.samplingFrequency
            * settings.DASHBOARD_MAX_CHART_POINTS
        )
    )
    return max(request.filter.fromDate, request.filter.toDate - visible_window)


async def get_chart_data(
    session: AsyncSession, request: ChartRequest
) -> list[dict[str, float | str]]:
    statement = text(
        "WITH sampled AS ("
        "SELECT m.sensor_id, m.value, m.time, m.measurement_id, "
        "s.chart_aggregation_method, "
        "timestamp 'epoch' + floor(extract(epoch FROM "
        "(m.time - timestamp 'epoch')) / "
        "GREATEST(:frequency, COALESCE(NULLIF(s.measurement_frequency, 0), :frequency))) "
        "* GREATEST(:frequency, COALESCE(NULLIF(s.measurement_frequency, 0), :frequency)) "
        "* interval '1 second' AS bucket "
        "FROM measurements m JOIN sensors s ON s.sensor_id = m.sensor_id "
        "JOIN data_sources ds ON ds.data_source_id = m.data_source_id "
        "WHERE m.sensor_id IN :sensor_ids AND ds.source_key = :source "
        "AND m.time >= :from_date AND m.time <= :to_date) "
        "SELECT bucket, sensor_id, CASE chart_aggregation_method "
        "WHEN 'average' THEN avg(value) "
        "WHEN 'minimum' THEN min(value) "
        "WHEN 'maximum' THEN max(value) "
        "ELSE last(value, time) END AS aggregated_value "
        "FROM sampled GROUP BY bucket, sensor_id, chart_aggregation_method "
        "ORDER BY bucket, sensor_id"
    ).bindparams(bindparam("sensor_ids", expanding=True))
    rows = (
        await session.execute(
            statement,
            {
                "sensor_ids": request.sensorIds,
                "source": request.source,
                "frequency": request.filter.samplingFrequency,
                "from_date": _chart_query_start(request),
                "to_date": request.filter.toDate,
            },
        )
    ).mappings()

    points: dict[datetime, dict[str, float | str]] = {}
    for row in rows:
        bucket = row["bucket"]
        point = points.setdefault(
            bucket, {"xAxis": bucket.isoformat(timespec="milliseconds")}
        )
        point[str(row["sensor_id"])] = float(row["aggregated_value"])
    return list(points.values())[-settings.DASHBOARD_MAX_CHART_POINTS :]


async def list_products(session: AsyncSession) -> list[ProductOut]:
    rows = (
        await session.execute(
            select(ProductType).order_by(ProductType.product_type_id)
        )
    ).scalars()
    return [
        ProductOut(id=row.product_type_name, maxQuantity=row.max_quantity)
        for row in rows
    ]


async def create_order(session: AsyncSession, body: OrderCreate) -> bool:
    requested = [product for product in body.products if (product.quantity or 0) > 0]
    names = [product.id for product in requested]
    product_types = (
        await session.execute(
            select(ProductType).where(ProductType.product_type_name.in_(names))
        )
    ).scalars().all()
    by_name = {product.product_type_name: product for product in product_types}
    missing = sorted(set(names) - set(by_name))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown product types: {', '.join(missing)}",
        )

    details = body.details
    order = Order(
        customer_name=details.customerName if details else None,
        fulfillment_date=details.fulfillmentDate if details else None,
        priority=details.priority if details else False,
    )
    session.add(order)
    await session.flush()

    for position, product in enumerate(requested, start=1):
        quantity = int(product.quantity or 0)
        item = OrderItem(
            order_id=order.order_id,
            product_type_id=by_name[product.id].product_type_id,
            position=position,
            requested_quantity=quantity,
        )
        session.add(item)
        await session.flush()
        session.add_all(
            ProductInstance(order_item_id=item.order_item_id, sequence_number=sequence)
            for sequence in range(1, quantity + 1)
        )
    await session.commit()
    return True


async def list_orders(session: AsyncSession, completed: bool) -> list[OrderListItem]:
    wanted_statuses = ["completed"] if completed else ["pending", "in_progress"]
    orders = (
        await session.execute(
            select(Order)
            .where(Order.status.in_(wanted_statuses))
            .order_by(Order.priority.desc(), Order.order_date.desc())
        )
    ).scalars()
    return [
        OrderListItem(
            orderID=str(order.order_id),
            customerName=order.customer_name,
            orderDate=_date_string(order.order_date),
            fulfillmentDate=_date_string(order.fulfillment_date),
            priority=order.priority,
        )
        for order in orders
    ]


async def get_order(
    session: AsyncSession, order_id: int | None = None
) -> OrderOut:
    if order_id is None:
        order = await session.scalar(
            select(Order)
            .where(Order.status.in_(["in_progress", "pending"]))
            .order_by(Order.status.desc(), Order.priority.desc(), Order.order_date)
            .limit(1)
        )
    else:
        order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    rows = (
        await session.execute(
            select(OrderItem, ProductType)
            .join(ProductType, ProductType.product_type_id == OrderItem.product_type_id)
            .where(OrderItem.order_id == order.order_id)
            .order_by(OrderItem.position)
        )
    ).all()
    return OrderOut(
        details=OrderListItem(
            orderID=str(order.order_id),
            customerName=order.customer_name,
            orderDate=_date_string(order.order_date),
            fulfillmentDate=_date_string(order.fulfillment_date),
            priority=order.priority,
        ),
        products=[
            ProductOut(
                id=product_type.product_type_name,
                quantity=item.requested_quantity,
                completedQuantity=item.completed_quantity,
                maxQuantity=product_type.max_quantity,
            )
            for item, product_type in rows
        ],
    )


async def cancel_order(session: AsyncSession, order_id: int) -> bool:
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status == "completed":
        raise HTTPException(status_code=409, detail="Completed order cannot be deleted")
    now = datetime.now()
    item_ids = select(OrderItem.order_item_id).where(OrderItem.order_id == order_id)
    product_ids = select(ProductInstance.product_instance_id).where(
        ProductInstance.order_item_id.in_(item_ids)
    )
    await session.execute(
        update(TrayProductAssignment)
        .where(
            TrayProductAssignment.product_instance_id.in_(product_ids),
            TrayProductAssignment.released_at.is_(None),
        )
        .values(released_at=now)
    )
    await session.execute(
        update(ProductInstance)
        .where(ProductInstance.order_item_id.in_(item_ids))
        .values(status="cancelled")
    )
    order.status = "cancelled"
    await session.commit()
    return True


async def complete_order(session: AsyncSession, order_id: int) -> bool:
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status == "cancelled":
        raise HTTPException(status_code=409, detail="Cancelled order cannot be completed")

    now = datetime.now()
    item_ids = select(OrderItem.order_item_id).where(OrderItem.order_id == order_id)
    product_ids = select(ProductInstance.product_instance_id).where(
        ProductInstance.order_item_id.in_(item_ids)
    )

    await session.execute(
        update(OrderItem)
        .where(OrderItem.order_id == order_id)
        .values(completed_quantity=OrderItem.requested_quantity)
    )
    await session.execute(
        update(ProductInstance)
        .where(ProductInstance.order_item_id.in_(item_ids))
        .values(
            status="completed",
            completed_at=func.coalesce(ProductInstance.completed_at, now),
        )
    )
    await session.execute(
        update(TrayProductAssignment)
        .where(
            TrayProductAssignment.product_instance_id.in_(product_ids),
            TrayProductAssignment.released_at.is_(None),
        )
        .values(released_at=now)
    )
    order.status = "completed"
    await session.commit()
    return True


async def list_kpis(
    session: AsyncSession, user_type_id: int, source: str = "real"
) -> list[BaseMetric]:
    rows = (
        await session.execute(
            text(
                "SELECT kd.kpi_id, kd.kpi_name, kd.kpi_unit, kd.value_type, latest.value "
                "FROM kpi_definitions kd "
                "JOIN LATERAL (SELECT kv.value FROM kpi_values kv "
                "LEFT JOIN data_sources ds ON ds.data_source_id = kv.data_source_id "
                "WHERE kv.kpi_id = kd.kpi_id "
                "AND (ds.source_key = :source OR kv.data_source_id IS NULL) "
                "ORDER BY kv.time DESC, kv.kpi_value_id DESC LIMIT 1) latest ON TRUE "
                "WHERE NOT EXISTS (SELECT 1 FROM user_type_kpis) "
                "OR EXISTS (SELECT 1 FROM user_type_kpis utk "
                "WHERE utk.kpi_id = kd.kpi_id AND utk.user_type_id = :user_type_id) "
                "ORDER BY kd.kpi_id"
            ),
            {"source": source, "user_type_id": user_type_id},
        )
    ).mappings()
    return [
        BaseMetric(
            id=int(row["kpi_id"]),
            name=str(row["kpi_name"]),
            unit=str(row["kpi_unit"]),
            value=float(row["value"]),
            type=row["value_type"],
        )
        for row in rows
    ]


async def assign_tray_to_next_product(
    session: AsyncSession, nfc_tag_id: str, order_id: int | None
) -> TrayAssignmentResult:
    normalized_tag = nfc_tag_id.strip().upper()
    if not normalized_tag:
        raise HTTPException(status_code=400, detail="NFC tag ID cannot be empty")

    tray = await session.scalar(
        select(Tray).where(Tray.nfc_tag_id == normalized_tag).with_for_update()
    )
    if tray is None:
        tray = Tray(nfc_tag_id=normalized_tag)
        session.add(tray)
        await session.flush()
    if not tray.enabled:
        raise HTTPException(status_code=409, detail="Tray is disabled")

    active_assignment = await session.scalar(
        select(TrayProductAssignment).where(
            TrayProductAssignment.tray_id == tray.tray_id,
            TrayProductAssignment.released_at.is_(None),
        )
    )
    if active_assignment is not None:
        raise HTTPException(status_code=409, detail="Tray already has an active product")

    product_query = (
        select(ProductInstance)
        .join(OrderItem, OrderItem.order_item_id == ProductInstance.order_item_id)
        .join(Order, Order.order_id == OrderItem.order_id)
        .where(
            ProductInstance.status == "queued",
            Order.status.in_(["pending", "in_progress"]),
        )
    )
    if order_id is not None:
        product_query = product_query.where(OrderItem.order_id == order_id)
    product = await session.scalar(
        product_query.order_by(
            Order.priority.desc(),
            Order.order_date,
            OrderItem.position,
            ProductInstance.sequence_number,
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if product is None:
        raise HTTPException(status_code=409, detail="No queued product is available")

    item = await session.get(OrderItem, product.order_item_id)
    assert item is not None
    order = await session.get(Order, item.order_id)
    if order is None or order.status not in {"pending", "in_progress"}:
        raise HTTPException(status_code=409, detail="Order is not active")

    assignment = TrayProductAssignment(
        tray_id=tray.tray_id, product_instance_id=product.product_instance_id
    )
    session.add(assignment)
    product.status = "assigned"
    order.status = "in_progress"
    await session.commit()
    return TrayAssignmentResult(
        trayId=tray.tray_id,
        nfcTagId=tray.nfc_tag_id,
        productInstanceId=product.product_instance_id,
        orderItemId=item.order_item_id,
        orderId=order.order_id,
    )


async def add_tracking_event(
    session: AsyncSession,
    product_instance_id: int,
    body: TrackingEventRequest,
    next_process_step_id: int | None = None,
) -> TrackingEventResult:
    if body.externalEventId is not None:
        existing = await session.scalar(
            select(ProductTrackingEvent).where(
                ProductTrackingEvent.external_event_id == body.externalEventId
            )
        )
        if existing is not None:
            if existing.product_instance_id != product_instance_id:
                raise HTTPException(
                    status_code=409,
                    detail="External event ID already belongs to another product",
                )
            return TrackingEventResult(
                eventId=existing.product_tracking_event_id,
                productInstanceId=existing.product_instance_id,
                state=existing.state,
                time=existing.time,
            )

    product = await session.scalar(
        select(ProductInstance)
        .where(ProductInstance.product_instance_id == product_instance_id)
        .with_for_update()
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product instance not found")
    if product.status in {"completed", "done", "cancelled"}:
        raise HTTPException(status_code=409, detail="Product is already terminal")

    assignment = await session.scalar(
        select(TrayProductAssignment).where(
            TrayProductAssignment.product_instance_id == product_instance_id,
            TrayProductAssignment.released_at.is_(None),
        )
    )
    if assignment is None:
        raise HTTPException(status_code=409, detail="Product has no active tray assignment")

    event_time = body.time or datetime.now()
    event = ProductTrackingEvent(
        product_instance_id=product_instance_id,
        process_step_id=body.processStepId,
        next_process_step_id=next_process_step_id,
        asset_id=body.assetId,
        tray_id=assignment.tray_id,
        time=event_time,
        state=body.state,
        external_event_id=body.externalEventId,
    )
    session.add(event)

    if body.state == "done":
        product.status = "done"
        product.completed_at = event_time
        assignment.released_at = event_time
        item = await session.get(OrderItem, product.order_item_id)
        assert item is not None
        completed_count = await session.scalar(
            select(func.count(ProductInstance.product_instance_id)).where(
                ProductInstance.order_item_id == item.order_item_id,
                ProductInstance.status.in_(["completed", "done"]),
            )
        )
        # The current product is already marked completed in this session.
        item.completed_quantity = int(completed_count or 0)
        await session.flush()
        incomplete_items = await session.scalar(
            select(func.count(OrderItem.order_item_id)).where(
                OrderItem.order_id == item.order_id,
                OrderItem.completed_quantity < OrderItem.requested_quantity,
            )
        )
        if int(incomplete_items or 0) == 0:
            order = await session.get(Order, item.order_id)
            assert order is not None
            order.status = "completed"
    elif body.state == "arrived":
        product.status = "in_progress"

    await session.flush()
    result = TrackingEventResult(
        eventId=event.product_tracking_event_id,
        productInstanceId=product_instance_id,
        state=body.state,
        time=event_time,
    )
    await session.commit()
    return result


async def add_station_tracking_event(
    session: AsyncSession, body: StationTrackingEventRequest
) -> TrackingEventResult:
    if body.nextStationKey is not None and (
        body.stationKey != "visual_qc" or body.state != "departed"
    ):
        raise HTTPException(
            status_code=422,
            detail="nextStationKey is only valid for a visual_qc departed event",
        )

    product = await session.get(ProductInstance, body.productInstanceId)
    if product is None:
        raise HTTPException(status_code=404, detail="Product instance not found")
    if product.order_item_id != body.orderItemId:
        raise HTTPException(
            status_code=409,
            detail="orderItemId does not belong to productInstanceId",
        )

    if body.state == "done":
        station_key = "warehouse"
        descending = True
    else:
        station_key = body.stationKey
        descending = body.state == "departed"

    order_direction = "DESC" if descending else "ASC"
    location = (
        await session.execute(
            text(
                "SELECT ps.process_step_id, a.asset_id "
                "FROM process_configurations pc "
                "JOIN asset_process_steps aps ON aps.process_configuration_id = pc.process_configuration_id "
                "JOIN assets a ON a.asset_id = aps.asset_id "
                "JOIN process_steps ps ON ps.process_step_id = aps.process_step_id "
                "WHERE pc.is_active AND a.asset_key = :station_key "
                f"ORDER BY ps.process_step_id {order_direction} LIMIT 1"
            ),
            {"station_key": station_key},
        )
    ).mappings().first()
    if location is None:
        raise HTTPException(
            status_code=422,
            detail=f"Station is not mapped in the active process: {station_key}",
        )

    next_process_step_id: int | None = None
    if body.state == "departed":
        source_step_id = int(location["process_step_id"])
        if body.nextStationKey is not None:
            route = (
                await session.execute(
                    text(
                        "WITH RECURSIVE active_configuration AS ("
                        "SELECT process_configuration_id FROM process_configurations "
                        "WHERE is_active ORDER BY valid_from DESC LIMIT 1), "
                        "paths(step_id, first_hop, depth, visited) AS ("
                        "SELECT r.next_process_step_id, r.next_process_step_id, 1, "
                        "ARRAY[r.process_step_id, r.next_process_step_id] "
                        "FROM routing r JOIN active_configuration ac "
                        "ON ac.process_configuration_id = r.process_configuration_id "
                        "WHERE r.process_step_id = :source_step_id "
                        "UNION ALL "
                        "SELECT r.next_process_step_id, p.first_hop, p.depth + 1, "
                        "p.visited || r.next_process_step_id "
                        "FROM paths p JOIN routing r ON r.process_step_id = p.step_id "
                        "JOIN active_configuration ac "
                        "ON ac.process_configuration_id = r.process_configuration_id "
                        "WHERE p.depth < 20 "
                        "AND NOT r.next_process_step_id = ANY(p.visited)) "
                        "SELECT p.first_hop FROM paths p "
                        "JOIN active_configuration ac ON TRUE "
                        "JOIN asset_process_steps aps "
                        "ON aps.process_configuration_id = ac.process_configuration_id "
                        "AND aps.process_step_id = p.step_id "
                        "JOIN assets a ON a.asset_id = aps.asset_id "
                        "WHERE a.asset_key = :next_station_key "
                        "ORDER BY p.depth LIMIT 1"
                    ),
                    {
                        "source_step_id": source_step_id,
                        "next_station_key": body.nextStationKey,
                    },
                )
            ).mappings().first()
            if route is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "No route exists from the current station to "
                        f"{body.nextStationKey}"
                    ),
                )
            next_process_step_id = int(route["first_hop"])
        else:
            routes = (
                await session.execute(
                    text(
                        "SELECT r.next_process_step_id FROM routing r "
                        "JOIN process_configurations pc "
                        "ON pc.process_configuration_id = r.process_configuration_id "
                        "WHERE pc.is_active AND r.process_step_id = :source_step_id "
                        "ORDER BY r.next_process_step_id"
                    ),
                    {"source_step_id": source_step_id},
                )
            ).scalars().all()
            if len(routes) != 1:
                raise HTTPException(
                    status_code=422,
                    detail="nextStationKey is required when the route has multiple branches",
                )
            next_process_step_id = int(routes[0])

    return await add_tracking_event(
        session,
        body.productInstanceId,
        TrackingEventRequest(
            state=body.state,
            processStepId=int(location["process_step_id"]),
            assetId=int(location["asset_id"]),
            time=body.time,
            externalEventId=body.eventId,
        ),
        next_process_step_id=next_process_step_id,
    )
