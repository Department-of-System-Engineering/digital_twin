from datetime import datetime, timedelta, timezone

import pytest

from app.sensor_statistics_backfill import backfill_sensor_statistics


class _ScalarValues:
    def all(self) -> list[int]:
        return [4, 5, 6]


class _Result:
    def scalars(self) -> _ScalarValues:
        return _ScalarValues()


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []

    def execute(self, statement, parameters=None) -> _Result:
        self.calls.append((str(statement), parameters))
        return _Result()


def test_backfill_aggregates_all_measurements_per_sensor() -> None:
    connection = _Connection()
    learning_time = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    )

    sensor_ids = backfill_sensor_statistics(
        connection,  # type: ignore[arg-type]
        learning_time=learning_time,
    )

    sql = "\n".join(statement for statement, _ in connection.calls)
    assert sensor_ids == [4, 5, 6]
    assert "AVG(measurement.value)" in sql
    assert "STDDEV_SAMP(measurement.value)" in sql
    assert "GROUP BY measurement.sensor_id" in sql
    assert connection.calls[-1][1] == {"learning_time": learning_time}


def test_backfill_rejects_future_learning_time() -> None:
    with pytest.raises(ValueError, match="must be in the past"):
        backfill_sensor_statistics(
            _Connection(),  # type: ignore[arg-type]
            learning_time=(
                datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(minutes=1)
            ),
        )
