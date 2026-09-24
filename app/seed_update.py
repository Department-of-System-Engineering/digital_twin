"""Manually import validated master data without recreating the database."""

import argparse
import logging

from sqlalchemy import text

from .db import sync_engine
from .maintenance.sensor_failure_sync import configure_sensor_failure_type_sync
from .seed_data import import_seed_workbook, load_seed_workbook
from .settings import settings


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("db-seed-update")

_IMPORT_LOCK_NAME = "digital_twin_seed_update"


def update_seed_data(*, validate_only: bool = False) -> None:
    """Validate the workbook and atomically upsert its master data."""

    seed = load_seed_workbook(settings.SEED_WORKBOOK_PATH)
    log.info(
        "Seed workbook validated: workbook=%s, rows=%s",
        seed.path,
        sum(len(rows) for rows in seed.sheets.values()),
    )
    if validate_only:
        return

    # One transaction guarantees that a failed import cannot leave a partially
    # updated set of master data. The advisory lock prevents concurrent imports.
    with sync_engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
            {"lock_name": _IMPORT_LOCK_NAME},
        )
        result = import_seed_workbook(connection, seed)
        inserted_relations = configure_sensor_failure_type_sync(connection)

    log.info(
        "Seed update committed: workbook=%s, rows=%s, "
        "new_sensor_failure_relations=%s",
        seed.path,
        result.total_rows,
        inserted_relations,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and atomically update database master data."
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the workbook without changing the database.",
    )
    args = parser.parse_args()
    update_seed_data(validate_only=args.validate_only)


if __name__ == "__main__":
    main()
