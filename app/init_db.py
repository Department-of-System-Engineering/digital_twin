import logging

from sqlalchemy import inspect, text

from .db import sync_engine
from .maintenance.sensor_failure_sync import configure_sensor_failure_type_sync
from .models import Base
from .seed_data import import_seed_workbook, load_seed_workbook
from .settings import settings


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("db-init")


REQUIRED_TABLES = {
    "asset_process_steps",
    "asset_failure_types",
    "asset_worksheet_lists",
    "assets",
    "data_sources",
    "datacollector_sync_state",
    "etas_betas",
    "failure_types",
    "gammas",
    "kpi_definitions",
    "kpi_values",
    "measurement_types",
    "measurements",
    "operations_done_lists",
    "order_items",
    "orders",
    "prediction_asset_failure_type_levels",
    "prediction_asset_levels",
    "prediction_jobs",
    "predictions",
    "process_configurations",
    "process_steps",
    "product_instances",
    "product_tracking_events",
    "product_types",
    "ranges",
    "sensor_failure_types",
    "sensor_statistics",
    "sensor_types",
    "sensors",
    "routing",
    "tray_product_assignments",
    "trays",
    "user_type_kpis",
    "user_types",
}


SCHEMA_UPDATES = (
    "DO $$ BEGIN "
    "IF EXISTS ("
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = 'assets' "
    "AND column_name = 'sf_asset_id'"
    ") AND NOT EXISTS ("
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = 'assets' "
    "AND column_name = 'cmms_asset_id'"
    ") THEN "
    "ALTER TABLE public.assets RENAME COLUMN sf_asset_id TO cmms_asset_id; "
    "END IF; END $$",
    "ALTER TABLE public.assets ADD COLUMN IF NOT EXISTS cmms_asset_id BIGINT",
    "ALTER TABLE public.assets ADD COLUMN IF NOT EXISTS dc_asset_id TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_assets_cmms_asset_id "
    "ON public.assets (cmms_asset_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_assets_dc_asset_id "
    "ON public.assets (dc_asset_id)",
    "DO $$ BEGIN "
    "IF EXISTS ("
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = 'sensors' "
    "AND column_name = 'metric_function_id' AND data_type <> 'text'"
    ") THEN "
    "ALTER TABLE public.sensors ALTER COLUMN metric_function_id TYPE TEXT "
    "USING metric_function_id::text; "
    "END IF; END $$",
    "ALTER TABLE public.measurement_types ADD COLUMN IF NOT EXISTS unit_name TEXT",
    "ALTER TABLE public.measurement_types ADD COLUMN IF NOT EXISTS unit_symbol TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_measurement_types_name_unit "
    "ON public.measurement_types (measurement_type_name, unit_name, unit_symbol)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_sensor_types_name_measurement_type "
    "ON public.sensor_types (type_name, measurement_type_id)",
    "CREATE TABLE IF NOT EXISTS public.datacollector_sync_state ("
    "sync_name CHARACTER VARYING(100) PRIMARY KEY, "
    "last_successful_time_to TIMESTAMP WITHOUT TIME ZONE, "
    "last_metrics_sync_at TIMESTAMP WITHOUT TIME ZONE"
    ")",
    "ALTER TABLE public.sensors ADD COLUMN IF NOT EXISTS "
    "chart_aggregation_method CHARACTER VARYING(16) NOT NULL DEFAULT 'latest'",
    "ALTER TABLE public.measurements ADD COLUMN IF NOT EXISTS data_source_id BIGINT",
)

PRE_SEED_SCHEMA_UPDATES = (
    "ALTER TABLE public.assets ADD COLUMN IF NOT EXISTS asset_key CHARACTER VARYING(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_assets_asset_key ON public.assets (asset_key)",
    "ALTER TABLE public.measurement_types ADD COLUMN IF NOT EXISTS measurement_type_key CHARACTER VARYING(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_measurement_types_key ON public.measurement_types (measurement_type_key)",
    "ALTER TABLE public.sensor_types ADD COLUMN IF NOT EXISTS sensor_type_key CHARACTER VARYING(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_sensor_types_key ON public.sensor_types (sensor_type_key)",
    "ALTER TABLE public.sensors ADD COLUMN IF NOT EXISTS sensor_key CHARACTER VARYING(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_sensors_key ON public.sensors (sensor_key)",
    "ALTER TABLE public.process_configurations ADD COLUMN IF NOT EXISTS configuration_key CHARACTER VARYING(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_process_configurations_key ON public.process_configurations (configuration_key)",
    "ALTER TABLE public.process_steps ADD COLUMN IF NOT EXISTS process_step_key CHARACTER VARYING(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_process_steps_key ON public.process_steps (process_step_key)",
    "ALTER TABLE public.product_types ADD COLUMN IF NOT EXISTS product_type_key CHARACTER VARYING(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_product_types_key ON public.product_types (product_type_key)",
    "ALTER TABLE public.user_types ADD COLUMN IF NOT EXISTS user_type_key CHARACTER VARYING(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_user_types_key ON public.user_types (user_type_key)",
)

POST_MODEL_SCHEMA_UPDATES = (
    "UPDATE public.measurements SET data_source_id = ("
    "SELECT data_source_id FROM public.data_sources WHERE source_key = 'real'"
    ") WHERE data_source_id IS NULL",
    "ALTER TABLE public.measurements ALTER COLUMN data_source_id SET NOT NULL",
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint "
    "WHERE conname = 'fk_measurements_data_sources') THEN "
    "ALTER TABLE public.measurements ADD CONSTRAINT fk_measurements_data_sources "
    "FOREIGN KEY (data_source_id) REFERENCES public.data_sources(data_source_id); "
    "END IF; END $$",
    "CREATE INDEX IF NOT EXISTS ix_measurements_sensor_source_time "
    "ON public.measurements (sensor_id, data_source_id, time DESC)",
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint "
    "WHERE conname = 'ck_sensors_chart_aggregation_method') THEN "
    "ALTER TABLE public.sensors ADD CONSTRAINT ck_sensors_chart_aggregation_method "
    "CHECK (chart_aggregation_method IN "
    "('average', 'latest', 'minimum', 'maximum')); END IF; END $$",
    "SELECT create_hypertable('public.kpi_values', by_range('time'), "
    "if_not_exists => TRUE)",
)

REQUIRED_HYPERTABLES = {
    "asset_worksheet_lists",
    "measurements",
    "kpi_values",
    "prediction_asset_failure_type_levels",
    "prediction_asset_levels",
}


def main() -> None:
    """Verify the SQL-managed TimescaleDB schema before app startup."""

    log.info("Verifying database schema")

    with sync_engine.connect() as connection:
        for statement in SCHEMA_UPDATES:
            connection.execute(text(statement))
        connection.commit()

        # Create dashboard tables on existing installations as well as on fresh ones.
        Base.metadata.create_all(connection, checkfirst=True)
        for statement in PRE_SEED_SCHEMA_UPDATES:
            connection.execute(text(statement))
        connection.commit()

        seed = load_seed_workbook(settings.SEED_WORKBOOK_PATH)
        import_result = import_seed_workbook(connection, seed)
        connection.commit()

        log.info(
            "Excel seed import complete: workbook=%s, rows=%s",
            seed.path,
            import_result.total_rows,
        )

        for statement in POST_MODEL_SCHEMA_UPDATES:
            connection.execute(text(statement))

        inserted_sensor_failure_types = configure_sensor_failure_type_sync(connection)
        connection.commit()

        log.info(
            "Sensor/failure-type synchronization complete: inserted=%s",
            inserted_sensor_failure_types,
        )

        existing_tables = set(
            inspect(connection).get_table_names(schema="public")
        )
        missing_tables = REQUIRED_TABLES - existing_tables

        if missing_tables:
            raise RuntimeError(
                "Missing database tables: "
                + ", ".join(sorted(missing_tables))
            )

        has_timescaledb = connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_extension "
                "WHERE extname = 'timescaledb'"
                ")"
            )
        ).scalar_one()

        if not has_timescaledb:
            raise RuntimeError("The timescaledb extension is not installed")

        hypertables = set(
            connection.execute(
                text(
                    "SELECT hypertable_name "
                    "FROM timescaledb_information.hypertables "
                    "WHERE hypertable_schema = 'public'"
                )
            ).scalars()
        )
        missing_hypertables = REQUIRED_HYPERTABLES - hypertables

        if missing_hypertables:
            raise RuntimeError(
                "Tables not configured as hypertables: "
                + ", ".join(sorted(missing_hypertables))
            )

        has_jobstatus = connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_type t "
                "JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = 'public' AND t.typname = 'jobstatus'"
                ")"
            )
        ).scalar_one()

        if not has_jobstatus:
            raise RuntimeError("The public.jobstatus enum is missing")

        connection.execute(
            text(
                "ALTER TYPE public.jobstatus "
                "ADD VALUE IF NOT EXISTS 'skipped'"
            )
        )
        connection.commit()

    log.info("Database schema is valid")


if __name__ == "__main__":
    main()
