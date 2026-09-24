# Database initialization from Excel

The authoritative application seed data is stored in:

```text
db/init/data/db_init_data.xlsx
```

`app.init_db` reads and validates this workbook after the database schema is
available. The import runs before the API, worker, and DataCollector start.
Docker Compose enforces this through the `db-init` dependency.

## Cold start sequence

1. PostgreSQL runs `db/init/001_schema.sql` on a new database volume.
2. `db/init/002_seed_data.sql` creates only the infrastructure-level `real`
   data source on ID 1. This preserves compatibility with direct database
   writers that use the default data source.
3. The one-shot `db-init` service applies compatible schema updates.
   Empty prediction tables using the obsolete hypertable layout are rebuilt
   as regular PostgreSQL tables. If legacy prediction rows exist, startup
   stops instead of deleting them and requires an explicit data migration.
4. `db-init` validates the complete Excel workbook before importing any row.
5. Reference data is upserted using stable keys such as `asset_key` and
   `process_step_key`. PostgreSQL continues to generate the numeric IDs.
6. Routing and asset/process-step relations are synchronized to the workbook
   for every configuration present in it.
7. Missing sensor/failure-type combinations are generated and database
   triggers are installed for later additions.

If validation or import fails, `db-init` exits unsuccessfully and the dependent
services do not start.

## Updating seed data

- Add records inside the Excel table on the appropriate worksheet.
- Every owning `*_key` must be unique and use lowercase letters, numbers,
  underscores, or hyphens.
- References must contain the target row's stable key, never a generated
  database ID.
- Do not change a stable key after it has been imported. Change the descriptive
  name instead.
- `cmms_asset_id`, `dc_asset_id`, and `metric_function_id` may remain blank.
  A blank value does not erase a mapping that is already stored in the database.
- Exactly one process configuration must have `is_active = TRUE`.
- `measurement_frequency` and `processing_time` are expressed in seconds.
- `chart_aggregation_method` must be `average`, `latest`, `minimum`, or
  `maximum`.
- `ranges_key` is reserved for a later `Ranges` worksheet and must remain blank
  until that importer extension is implemented.

The importer updates or creates master data, but does not delete master rows.
For configurations present in the workbook, it does replace routing and
asset/process-step relation rows so removed or changed edges do not remain
active accidentally.

## Running the import

With Docker Compose:

```powershell
docker compose run --rm db-init
```

For later master-data additions on a running installation, use the dedicated
transactional command instead of recreating the database:

```bash
python3 scripts/database_admin.py seed-validate
python3 scripts/database_admin.py seed-update
```

Manual full-database backup and verification are documented in
`docs/database_maintenance.md`.

To rebuild a completely empty local database volume:

```powershell
docker compose down -v
docker compose up --build -d
```

The first command in the block permanently removes the Compose-managed database
volume and must only be used when its contents are no longer needed.

The workbook location is configured by `SEED_WORKBOOK_PATH`. Inside Docker it
defaults to `/app/db/init/data/db_init_data.xlsx`.

## Extending the workbook format

Sheet contracts are defined in `SHEET_SPECS` in `app/seed_data.py`. To add a new
kind of initialization data:

1. add a worksheet containing one Excel table;
2. add its required headers and stable-key rule to `SHEET_SPECS`;
3. add reference and value validation to `_validate_seed`;
4. add its upsert after all referenced entities have been imported;
5. add a parser/import test.

Unknown extra columns are tolerated, so columns can be prepared in Excel before
the importer starts using them. Missing required sheets or columns stop startup
with a cell-oriented validation message.
