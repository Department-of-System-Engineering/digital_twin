"""One-shot sensor statistics backfill from stored measurements.

This module is deliberately isolated so the temporary administrative command
can be removed after existing installations have been backfilled.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sensor-statistics-backfill")

_LOCK_NAME = "digital_twin_sensor_statistics_backfill"


def default_learning_time() -> datetime:
    """Return a naive UTC timestamp that is guaranteed to be in the past."""

    return (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    )


def backfill_sensor_statistics(
    connection: Connection,
    *,
    learning_time: datetime,
) -> list[int]:
    """Insert one statistics version for every sensor having measurements."""

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if learning_time >= now_utc:
        raise ValueError("learning_time must be in the past")

    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
        {"lock_name": _LOCK_NAME},
    )
    result = connection.execute(
        text(
            """
            INSERT INTO public.sensor_statistics
                (sensor_id, standard_deviation_value, average_value, learning_time)
            SELECT
                measurement.sensor_id,
                COALESCE(STDDEV_SAMP(measurement.value), 0.0),
                AVG(measurement.value),
                :learning_time
            FROM public.measurements AS measurement
            GROUP BY measurement.sensor_id
            ORDER BY measurement.sensor_id
            RETURNING sensor_id
            """
        ),
        {"learning_time": learning_time},
    )
    return [int(sensor_id) for sensor_id in result.scalars().all()]


def run_backfill(*, learning_time: datetime | None = None) -> list[int]:
    """Run the backfill in one transaction against the configured database."""

    from .db import sync_engine

    effective_learning_time = learning_time or default_learning_time()
    with sync_engine.begin() as connection:
        sensor_ids = backfill_sensor_statistics(
            connection,
            learning_time=effective_learning_time,
        )

    if not sensor_ids:
        raise RuntimeError("No measurements were found; no statistics were inserted")

    log.info(
        "Sensor statistics backfill committed: sensors=%s, learning_time=%s",
        len(sensor_ids),
        effective_learning_time.isoformat(sep=" "),
    )
    return sensor_ids


def _parse_learning_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "learning time must be an ISO date/time"
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Insert per-sensor average and sample standard deviation from all "
            "stored measurements."
        )
    )
    parser.add_argument(
        "--learning-time",
        type=_parse_learning_time,
        help="Past ISO timestamp; defaults to one second before command start.",
    )
    args = parser.parse_args()
    run_backfill(learning_time=args.learning_time)


if __name__ == "__main__":
    main()
