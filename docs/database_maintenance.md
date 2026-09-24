# Manual database maintenance

The commands in this document run only when an operator explicitly invokes
them. No scheduled or startup backup is configured.

## Adding or updating master data

Add rows to `db/init/data/db_init_data.xlsx` using stable keys, then validate
the complete workbook without writing to PostgreSQL:

```bash
python3 scripts/database_admin.py seed-validate
```

Apply the validated workbook:

```bash
python3 scripts/database_admin.py seed-update
```

The maintenance container reads the current host-side workbook through a
read-only bind mount, so changing Excel data does not require rebuilding the
Docker image. Importer code changes still require an image rebuild.

The update uses PostgreSQL upserts and runs in one transaction. Existing
operational rows such as measurements, orders, product tracking and prediction
history are not deleted. A failure rolls back the complete update. An advisory
database lock prevents two seed imports from running concurrently.

Master rows omitted from the workbook are not deleted. For each process
configuration included in the workbook, routing and asset/process-step links
are intentionally synchronized to the workbook, so removing such a relation
from Excel removes that relation from the database during the update.

The equivalent direct Compose commands are:

```bash
docker compose run --rm db-seed-update python -m app.seed_update --validate-only
docker compose run --rm db-seed-update
```

## Creating a full backup manually

The database container must be running. Start the backup explicitly with:

```bash
python3 scripts/database_admin.py backup
```

The command creates a timestamped PostgreSQL custom-format archive below
`backups/`. Before publishing the file it runs `pg_restore --list` against the
archive. It also creates a matching `.sha256` checksum file. The `backups/`
directory is ignored by Git.

To use another destination, preferably a disk outside the repository:

```bash
python3 scripts/database_admin.py backup --output-dir /srv/dt-backups
```

A successful command prints the exact archive path and checksum. Copy both
files to storage independent from the server; a dump left only on the same disk
does not protect against disk failure.

The dump contains the complete configured application database: schema,
table data, indexes, constraints and TimescaleDB-managed objects. PostgreSQL
cluster-wide roles and other databases are outside the scope of this archive.
Restoration should first be tested into a separate database as described in
`docs/containerization.md`.
