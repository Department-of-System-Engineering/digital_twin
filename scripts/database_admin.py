"""Explicit database maintenance commands for Docker Compose deployments."""

from __future__ import annotations

import argparse
import hashlib
import os
from datetime import datetime
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(
    arguments: list[str], *, capture_output: bool = False
) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", *arguments]
    try:
        return subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=True,
            text=True,
            capture_output=capture_output,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Docker Compose is not available on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Command failed with exit code {exc.returncode}: {' '.join(command)}"
        ) from exc


def validate_seed() -> None:
    _run(
        [
            "run",
            "--rm",
            "db-seed-update",
            "python",
            "-m",
            "app.seed_update",
            "--validate-only",
        ]
    )


def update_seed() -> None:
    _run(["run", "--rm", "db-seed-update"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backup_database(output_directory: Path) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S%z")
    archive_name = f"digital_twin_{timestamp}.dump"
    remote_path = f"/tmp/{archive_name}"
    final_path = output_directory / archive_name
    partial_path = output_directory / f".{archive_name}.partial"

    if final_path.exists() or partial_path.exists():
        raise RuntimeError(f"Backup target already exists: {final_path}")

    dump_command = (
        "umask 077; "
        f'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" '
        f'--format=custom --file="{remote_path}"'
    )

    try:
        _run(["exec", "-T", "db", "sh", "-eu", "-c", dump_command])
        _run(["exec", "-T", "db", "pg_restore", "--list", remote_path])
        _run(["cp", f"db:{remote_path}", str(partial_path)])
        partial_path.replace(final_path)
        try:
            os.chmod(final_path, 0o600)
        except OSError:
            # Some Windows filesystems do not support POSIX permission bits.
            pass

        checksum = _sha256(final_path)
        checksum_path = final_path.with_suffix(final_path.suffix + ".sha256")
        checksum_path.write_text(
            f"{checksum}  {final_path.name}\n",
            encoding="ascii",
        )
        print(f"Backup created and verified: {final_path}")
        print(f"SHA-256: {checksum}")
        return final_path
    finally:
        partial_path.unlink(missing_ok=True)
        try:
            _run(["exec", "-T", "db", "rm", "-f", remote_path])
        except RuntimeError:
            # Keep the original dump/copy error as the primary failure.
            pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manual master-data update and database backup commands."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed-validate", help="Validate the Excel seed only.")
    subparsers.add_parser("seed-update", help="Atomically upsert Excel master data.")
    backup_parser = subparsers.add_parser(
        "backup", help="Create and verify a full PostgreSQL custom-format dump."
    )
    backup_parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "backups",
        help="Backup directory (default: PROJECT_ROOT/backups).",
    )

    args = parser.parse_args()
    try:
        if args.command == "seed-validate":
            validate_seed()
        elif args.command == "seed-update":
            update_seed()
        else:
            backup_database(args.output_dir.resolve())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
