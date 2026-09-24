# Camera QC station integration

The QC station runs natively on the Raspberry Pi. It has no database, database
credentials, Docker deployment, or independent HTTP server. It sends measurements
directly to this digital twin API. All orders, variant mappings, inspections,
findings, tracking events and product statuses live in the existing shared database.

## Backend deployment

Deploy the updated digital_twin application and run the existing db-init process.
The QC tables are part of the main SQLAlchemy model and `db/init/001_schema.sql`.
The existing db-init process creates qc_variant_mapping, qc_jobs, qc_inspections and
qc_findings in the SAME database and enables done/rework product statuses. There is
no QC schema installer, database service, connection URL, secret, port or Compose
service. If updating a running installation,
back up the central database first and allow the schema migration to finish before
starting the updated API and camera clients.

The existing INBOUND_API_KEY protects ordinary /qc endpoints. Administrative
mapping and cancellation operations require the existing MAPPING_ADMIN_API_KEY.
The station's QC_API_KEY must equal INBOUND_API_KEY, never the admin key.

## Endpoints on the existing API port

- POST /qc/claim with {"station_id":"tangram-qc-01"}: return the pending arrival
  at visual_qc with an immutable inspection ID and expected variant, or null.
  Reject ambiguous occupancy, unknown variant mappings, or stale assignments.
- POST /qc/results: persist the full station result; Idempotency-Key must equal
  inspection_id. Return {"inspection_id":...,"accepted":true,"duplicate":...}
  only after the shared database transaction commits. Same ID with a changed payload
  is rejected, and a product which already left the claimed arrival is not changed.
- GET /qc/results/{inspection_id}: central measurement, or 404 before acceptance.
- GET /qc/variants: central product type list with configured A/B/C/D variant.
- PUT /qc/variants/{product_type_id} with {"variant":"A"}: admin-only mapping.
  Configure all actual product types through this endpoint; no station-side SQL.
- POST /qc/jobs/{inspection_id}/cancel with {"reason":"operator removed tray"}:
  admin-only, audited recovery of an outstanding claim; does not change product state.
  Stop the station and review its pending JSON delivery files before cancelling.

manual results have no product identity and never change orders. For order results,
PASS sets product.status=done, records the warehouse done event, releases the tray,
and updates order completion counts atomically. FAIL sets rework and keeps the tray.
INCONCLUSIVE logs a measurement problem without changing product state. Existing
completed product statuses remain supported; fully fulfilled orders stay completed.

Keep the physical tray at QC until result acceptance. Node-RED/PLC owns the association
between the physical NFC tray and the visual_qc arrived tracking event. Rework routing
still uses the existing departed event with nextStationKey=assembly1 or assembly2.
Every reinspection needs a new arrived event, including a retry after INCONCLUSIVE.
The camera does not infer physical identity from color and does not select a random
pending order. There is one physical visual_qc work area.

The camera persists only transport JSON and its safety video locally. The server
uses qc_inspections for the result, qc_findings for categorized observations and
qc_jobs for the arrival association. Videos are not uploaded by this integration.
Findings summarize observed failing frames; a transient observation can coexist
with an overall PASS. Per-part metrics/edge relations are from the closing frame.

Tests: set TEST_DATABASE_URL to an isolated PostgreSQL test database and run
python -m unittest discover -s tests -p test_qc_api.py -v. Tests create and drop only
a random test schema. The shared-pool test exercises this application's existing
SQLAlchemy/psycopg connection pool, not a separate QC database connection.
