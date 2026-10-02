"""Keep initial prediction parameters complete for every failure relation."""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session


DatabaseExecutor = Connection | Session

INITIAL_LEARNING_TIME = "1970-01-01 00:00:00"


_BACKFILL_STATEMENTS = (
    """
    INSERT INTO public.etas_betas
        (eta_value, beta_value, asset_failure_type_id, learning_time)
    SELECT
        1.0,
        1.0,
        relation.asset_failure_type_id,
        TIMESTAMP '1970-01-01 00:00:00'
    FROM public.asset_failure_types AS relation
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.etas_betas AS parameter
        WHERE parameter.asset_failure_type_id = relation.asset_failure_type_id
    )
    """,
    """
    INSERT INTO public.gammas
        (gamma_value, sensor_failure_type_id, contribution, learning_time)
    SELECT
        1.0,
        relation.sensor_failure_type_id,
        0.0,
        TIMESTAMP '1970-01-01 00:00:00'
    FROM public.sensor_failure_types AS relation
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.gammas AS parameter
        WHERE parameter.sensor_failure_type_id = relation.sensor_failure_type_id
    )
    """,
)


_TRIGGER_STATEMENTS = (
    """
    CREATE OR REPLACE FUNCTION public.add_initial_eta_beta()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        INSERT INTO public.etas_betas
            (eta_value, beta_value, asset_failure_type_id, learning_time)
        VALUES (
            1.0,
            1.0,
            NEW.asset_failure_type_id,
            TIMESTAMP '1970-01-01 00:00:00'
        );

        RETURN NEW;
    END;
    $$
    """,
    """
    DROP TRIGGER IF EXISTS trg_add_initial_eta_beta
    ON public.asset_failure_types
    """,
    """
    CREATE TRIGGER trg_add_initial_eta_beta
    AFTER INSERT ON public.asset_failure_types
    FOR EACH ROW
    EXECUTE FUNCTION public.add_initial_eta_beta()
    """,
    """
    CREATE OR REPLACE FUNCTION public.add_initial_gamma()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        INSERT INTO public.gammas
            (gamma_value, sensor_failure_type_id, contribution, learning_time)
        VALUES (
            1.0,
            NEW.sensor_failure_type_id,
            0.0,
            TIMESTAMP '1970-01-01 00:00:00'
        );

        RETURN NEW;
    END;
    $$
    """,
    """
    DROP TRIGGER IF EXISTS trg_add_initial_gamma
    ON public.sensor_failure_types
    """,
    """
    CREATE TRIGGER trg_add_initial_gamma
    AFTER INSERT ON public.sensor_failure_types
    FOR EACH ROW
    EXECUTE FUNCTION public.add_initial_gamma()
    """,
)


def configure_prediction_parameter_defaults(
    executor: DatabaseExecutor,
) -> tuple[int, int]:
    """Backfill missing defaults and install triggers for future relations."""

    inserted_counts: list[int] = []
    for statement in _BACKFILL_STATEMENTS:
        result = executor.execute(text(statement))
        inserted_counts.append(max(result.rowcount or 0, 0))

    for statement in _TRIGGER_STATEMENTS:
        executor.execute(text(statement))

    return inserted_counts[0], inserted_counts[1]
