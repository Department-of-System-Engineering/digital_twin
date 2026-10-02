from datetime import datetime
from pathlib import Path

from app.seed_data import import_seed_workbook, load_seed_workbook


WORKBOOK_PATH = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "init"
    / "data"
    / "db_init_data.xlsx"
)


def test_packaged_seed_workbook_is_valid() -> None:
    seed = load_seed_workbook(WORKBOOK_PATH)

    assert len(seed.rows("Assets")) == 7
    assert len(seed.rows("MeasurementTypes")) == 5
    assert len(seed.rows("SensorTypes")) == 5
    assert len(seed.rows("Sensors")) == 17
    assert len(seed.rows("ProcessSteps")) == 10
    assert len(seed.rows("Routing")) == 11
    assert len(seed.rows("FailureTypes")) == 3
    assert len(seed.rows("AssetFailureTypes")) == 4
    assert len(seed.rows("SensorFailureTypes")) == 51
    assert len(seed.rows("SensorStatistics")) == 3
    assert len(seed.rows("Measurements")) == 2880
    assert len(seed.rows("EtaBetas")) == 4
    assert len(seed.rows("Gammas")) == 51
    assert len(seed.rows("AssetWorksheetLists")) == 6
    assert len(seed.rows("OperationsDoneLists")) == 22


def test_future_external_identifier_columns_are_present() -> None:
    seed = load_seed_workbook(WORKBOOK_PATH)

    assert all("cmms_asset_id" in row for row in seed.rows("Assets"))
    assert all("dc_asset_id" in row for row in seed.rows("Assets"))
    assert all("metric_function_id" in row for row in seed.rows("Sensors"))


def test_corrected_measurement_units_are_loaded() -> None:
    seed = load_seed_workbook(WORKBOOK_PATH)
    units = {
        row["measurement_type_key"]: row["unit"]
        for row in seed.rows("MeasurementTypes")
    }

    assert units == {
        "cylinder": "BOOL",
        "tray_track": "BOOL",
        "temperature": "°C",
        "humidity": "%",
        "acceleration": "none",
    }


class _ScalarResult:
    def __init__(self, value: int | None) -> None:
        self.value = value

    def scalar_one(self) -> int:
        assert self.value is not None
        return self.value

    def scalar_one_or_none(self) -> int | None:
        return self.value


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict | None]] = []
        self.next_id = 1

    def execute(self, statement, parameters=None) -> _ScalarResult:
        sql = str(statement)
        self.statements.append((sql, parameters))
        if sql.lstrip().startswith("SELECT"):
            return _ScalarResult(None)
        if "RETURNING" in sql:
            value = self.next_id
            self.next_id += 1
            return _ScalarResult(value)
        return _ScalarResult(None)


def test_complete_workbook_can_be_planned_for_import() -> None:
    seed = load_seed_workbook(WORKBOOK_PATH)
    connection = _RecordingConnection()

    result = import_seed_workbook(connection, seed)  # type: ignore[arg-type]

    sql = "\n".join(statement for statement, _ in connection.statements)
    assert result.total_rows == 3101
    assert sql.count("INSERT INTO public.asset_process_steps") == 10
    assert sql.count("INSERT INTO public.routing") == 11
    assert sql.count("INSERT INTO public.sensor_failure_types") == 51
    assert sql.count("INSERT INTO public.measurements") == 2880
    assert sql.count("INSERT INTO public.etas_betas") == 4
    assert sql.count("INSERT INTO public.gammas") == 51
    assert "UPDATE public.etas_betas" in sql
    assert "UPDATE public.gammas" not in sql
    assert sql.count("INSERT INTO public.asset_worksheet_lists") == 6
    assert sql.count("INSERT INTO public.operations_done_lists") == 22
    assert "INSERT INTO public.predictions" not in sql
    assert "COALESCE(EXCLUDED.cmms_asset_id, assets.cmms_asset_id)" in sql
    assert "ALTER COLUMN data_source_id" in sql


def test_prediction_parameters_start_with_dummy_values() -> None:
    seed = load_seed_workbook(WORKBOOK_PATH)

    assert {
        (row["eta_value"], row["beta_value"])
        for row in seed.rows("EtaBetas")
    } == {(10000, 1)}
    assert {row["gamma_value"] for row in seed.rows("Gammas")} == {1}
    assert {row["contribution"] for row in seed.rows("Gammas")} == {0}
    expected_learning_time = datetime(2026, 9, 1)
    assert {
        row["learning_time"] for row in seed.rows("EtaBetas")
    } == {expected_learning_time}
    assert {
        row["learning_time"] for row in seed.rows("Gammas")
    } == {expected_learning_time}
