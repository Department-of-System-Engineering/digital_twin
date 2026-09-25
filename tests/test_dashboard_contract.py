from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.dashboard.schemas import (
    ChartRequest,
    OrderCreate,
    StationTrackingEventRequest,
    TrackingEventRequest,
)
from app.dashboard.service import _chart_query_start, _number_type, complete_order


def test_chart_request_preserves_dashboard_contract_and_deduplicates_sensors() -> None:
    request = ChartRequest.model_validate(
        {
            "sensorIds": [7, 7, 9],
            "filter": {
                "samplingFrequency": 0.1,
                "fromDate": "2026-09-11T12:00:00",
                "toDate": "2026-09-11T12:01:00",
            },
        }
    )

    assert request.sensorIds == [7, 9]
    assert request.source == "real"


def test_chart_request_rejects_reversed_range() -> None:
    with pytest.raises(ValidationError):
        ChartRequest.model_validate(
            {
                "sensorIds": [7],
                "filter": {
                    "samplingFrequency": 1,
                    "fromDate": "2026-09-11T12:01:00",
                    "toDate": "2026-09-11T12:00:00",
                },
            }
        )


def test_station_tracking_event_uses_stable_keys() -> None:
    event = StationTrackingEventRequest.model_validate(
        {
            "eventId": "visual-qc-return-42",
            "productInstanceId": 42,
            "orderItemId": 7,
            "stationKey": "visual_qc",
            "nextStationKey": "assembly2",
            "state": "departed",
            "time": "2026-09-24T12:34:56",
        }
    )

    assert event.stationKey == "visual_qc"
    assert event.productInstanceId == 42
    assert event.nextStationKey == "assembly2"


def test_chart_history_is_bounded_to_display_capacity(monkeypatch) -> None:
    request = ChartRequest.model_validate(
        {
            "sensorIds": [7],
            "filter": {
                "samplingFrequency": 1,
                "fromDate": "2026-09-11T10:00:00",
                "toDate": "2026-09-11T12:00:00",
            },
        }
    )
    monkeypatch.setattr(
        "app.dashboard.service.settings.DASHBOARD_MAX_CHART_POINTS",
        60,
    )

    assert _chart_query_start(request).isoformat() == "2026-09-11T11:59:00"


def test_order_requires_at_least_one_positive_quantity() -> None:
    with pytest.raises(ValidationError):
        OrderCreate.model_validate({"products": [{"id": "A", "quantity": 0}]})


def test_dashboard_number_types_are_derived_without_changing_sensor_output() -> None:
    assert _number_type("INTEGER", "pcs") == "int"
    assert _number_type("DOUBLE", "%") == "percent"
    assert _number_type("DOUBLE", "bar") == "float"


def test_done_is_a_supported_terminal_tracking_event() -> None:
    event = TrackingEventRequest.model_validate({"state": "done"})
    assert event.state == "done"


@pytest.mark.asyncio
async def test_manual_order_completion_closes_all_related_work() -> None:
    order = SimpleNamespace(status="in_progress")
    session = AsyncMock()
    session.get.return_value = order

    assert await complete_order(session, 42) is True

    statements = [call.args[0] for call in session.execute.await_args_list]
    assert [statement.table.name for statement in statements] == [
        "order_items",
        "product_instances",
        "tray_product_assignments",
    ]
    assert order.status == "completed"
    session.scalar.assert_not_awaited()
    session.commit.assert_awaited_once()
