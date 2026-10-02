"""Keep initial prediction parameters complete for every failure relation."""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session


DatabaseExecutor = Connection | Session

_BACKFILL_STATEMENTS = (
    """
    UPDATE public.etas_betas
    SET eta_value = 10000.0,
        beta_value = 1.0
    WHERE learning_time = TIMESTAMP '2026-09-01 00:00:00'
    """,
    """
    INSERT INTO public.etas_betas
        (eta_value, beta_value, asset_failure_type_id, learning_time)
    SELECT
        10000.0,
        1.0,
        relation.asset_failure_type_id,
        TIMESTAMP '2026-09-01 00:00:00'
    FROM public.asset_failure_types AS relation
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.etas_betas AS parameter
        WHERE parameter.asset_failure_type_id = relation.asset_failure_type_id
          AND parameter.learning_time = TIMESTAMP '2026-09-01 00:00:00'
    )
    """,
    """
    UPDATE public.gammas
    SET gamma_value = 1.0,
        contribution = 0.0
    WHERE learning_time = TIMESTAMP '2026-09-01 00:00:00'
    """,
    """
    INSERT INTO public.gammas
        (gamma_value, sensor_failure_type_id, contribution, learning_time)
    SELECT
        1.0,
        relation.sensor_failure_type_id,
        0.0,
        TIMESTAMP '2026-09-01 00:00:00'
    FROM public.sensor_failure_types AS relation
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.gammas AS parameter
        WHERE parameter.sensor_failure_type_id = relation.sensor_failure_type_id
          AND parameter.learning_time = TIMESTAMP '2026-09-01 00:00:00'
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
            10000.0,
            1.0,
            NEW.asset_failure_type_id,
            TIMESTAMP '2026-09-01 00:00:00'
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
            TIMESTAMP '2026-09-01 00:00:00'
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

    executor.execute(text(_BACKFILL_STATEMENTS[0]))
    inserted_eta_betas = executor.execute(
        text(_BACKFILL_STATEMENTS[1])
    )
    executor.execute(text(_BACKFILL_STATEMENTS[2]))
    inserted_gammas = executor.execute(text(_BACKFILL_STATEMENTS[3]))

    for statement in _TRIGGER_STATEMENTS:
        executor.execute(text(statement))

    return (
        max(inserted_eta_betas.rowcount or 0, 0),
        max(inserted_gammas.rowcount or 0, 0),
    )
