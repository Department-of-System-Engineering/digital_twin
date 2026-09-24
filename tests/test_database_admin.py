from pathlib import Path
from types import SimpleNamespace

from scripts import database_admin


def test_backup_creates_verified_archive_and_checksum(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], *, capture_output: bool = False):
        calls.append(arguments)
        if arguments[0] == "cp":
            Path(arguments[2]).write_bytes(b"test database dump")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(database_admin, "_run", fake_run)

    archive = database_admin.backup_database(tmp_path)

    assert archive.exists()
    assert archive.suffix == ".dump"
    checksum = archive.with_suffix(".dump.sha256")
    assert checksum.exists()
    assert archive.name in checksum.read_text(encoding="ascii")
    assert any(call[:5] == ["exec", "-T", "db", "pg_restore", "--list"] for call in calls)
    assert calls[-1][:5] == ["exec", "-T", "db", "rm", "-f"]


def test_seed_update_uses_dedicated_compose_service(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        database_admin,
        "_run",
        lambda arguments, **_: calls.append(arguments),
    )

    database_admin.update_seed()

    assert calls == [["run", "--rm", "db-seed-update"]]
