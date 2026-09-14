"""Keep the sensor-to-failure-type relation complete."""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session


DatabaseExecutor = Connection | Session


_PREPARE_RELATION_STATEMENTS = (
    # Preserve Gamma references before removing any historical duplicate pairs.
    """
    WITH ranked_pairs AS (
        SELECT
            sensor_failure_type_id,
            min(sensor_failure_type_id) OVER (
                PARTITION BY sensor_id, failure_type_id
            ) AS retained_id
        FROM public.sensor_failure_types
    )
    UPDATE public.gammas AS gamma
    SET sensor_failure_type_id = ranked.retained_id
    FROM ranked_pairs AS ranked
    WHERE gamma.sensor_failure_type_id = ranked.sensor_failure_type_id
      AND ranked.sensor_failure_type_id <> ranked.retained_id
    """,
    """
    DELETE FROM public.sensor_failure_types AS relation
    USING public.sensor_failure_types AS retained
    WHERE relation.sensor_id = retained.sensor_id
      AND relation.failure_type_id = retained.failure_type_id
      AND relation.sensor_failure_type_id > retained.sensor_failure_type_id
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_sensor_failure_types_sensor_failure
    ON public.sensor_failure_types (sensor_id, failure_type_id)
    """,
)


_TRIGGER_STATEMENTS = (
    """
    CREATE OR REPLACE FUNCTION public.link_new_sensor_to_failure_types()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        INSERT INTO public.sensor_failure_types (sensor_id, failure_type_id)
        SELECT NEW.sensor_id, failure_type.failure_type_id
        FROM public.failure_types AS failure_type
        ON CONFLICT (sensor_id, failure_type_id) DO NOTHING;

        RETURN NEW;
    END;
    $$
    """,
    """
    DROP TRIGGER IF EXISTS trg_link_new_sensor_to_failure_types
    ON public.sensors
    """,
    """
    CREATE TRIGGER trg_link_new_sensor_to_failure_types
    AFTER INSERT ON public.sensors
    FOR EACH ROW
    EXECUTE FUNCTION public.link_new_sensor_to_failure_types()
    """,
    """
    CREATE OR REPLACE FUNCTION public.link_new_failure_type_to_sensors()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        INSERT INTO public.sensor_failure_types (sensor_id, failure_type_id)
        SELECT sensor.sensor_id, NEW.failure_type_id
        FROM public.sensors AS sensor
        ON CONFLICT (sensor_id, failure_type_id) DO NOTHING;

        RETURN NEW;
    END;
    $$
    """,
    """
    DROP TRIGGER IF EXISTS trg_link_new_failure_type_to_sensors
    ON public.failure_types
    """,
    """
    CREATE TRIGGER trg_link_new_failure_type_to_sensors
    AFTER INSERT ON public.failure_types
    FOR EACH ROW
    EXECUTE FUNCTION public.link_new_failure_type_to_sensors()
    """,
)


def synchronize_sensor_failure_types(executor: DatabaseExecutor) -> int:
    """Insert every missing sensor/failure-type pair and return their count."""

    result = executor.execute(
        text(
            """
            INSERT INTO public.sensor_failure_types (sensor_id, failure_type_id)
            SELECT sensor.sensor_id, failure_type.failure_type_id
            FROM public.sensors AS sensor
            CROSS JOIN public.failure_types AS failure_type
            ON CONFLICT (sensor_id, failure_type_id) DO NOTHING
            """
        )
    )
    return max(result.rowcount or 0, 0)


def configure_sensor_failure_type_sync(executor: DatabaseExecutor) -> int:
    """Backfill the relation and install triggers for future inserts."""

    for statement in _PREPARE_RELATION_STATEMENTS:
        executor.execute(text(statement))

    inserted_count = synchronize_sensor_failure_types(executor)

    for statement in _TRIGGER_STATEMENTS:
        executor.execute(text(statement))

    return inserted_count
