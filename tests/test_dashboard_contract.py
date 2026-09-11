import pytest
from pydantic import ValidationError

from app.dashboard.schemas import ChartRequest, OrderCreate, TrackingEventRequest
from app.dashboard.service import _number_type


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
