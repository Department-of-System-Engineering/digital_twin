from app.maintenance.prediction_parameter_defaults import (
    configure_prediction_parameter_defaults,
)


class _Result:
    def __init__(self, rowcount: int | None = None) -> None:
        self.rowcount = rowcount


class _RecordingExecutor:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if sql.lstrip().startswith("INSERT INTO public.etas_betas"):
            return _Result(3)
        if sql.lstrip().startswith("INSERT INTO public.gammas"):
            return _Result(12)
        return _Result()


def test_defaults_are_backfilled_and_kept_complete_by_triggers() -> None:
    executor = _RecordingExecutor()

    inserted = configure_prediction_parameter_defaults(executor)  # type: ignore[arg-type]

    sql = "\n".join(executor.statements)
    assert inserted == (3, 12)
    assert "SET eta_value = 10000.0" in sql
    assert "10000.0,\n        1.0" in sql
    assert "INSERT INTO public.gammas" in sql
    assert "CREATE TRIGGER trg_add_initial_eta_beta" in sql
    assert "CREATE TRIGGER trg_add_initial_gamma" in sql
    assert sql.count("TIMESTAMP '2026-09-01 00:00:00'") == 5
    assert "WHERE NOT EXISTS" in sql
