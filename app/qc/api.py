"""QC endpoints in the digital twin API. All product state stays in its database."""
import json
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contextlib import contextmanager
from .findings import findings
from ..db import sync_engine
from ..maintenance.security import require_api_key, require_mapping_admin_api_key

router = APIRouter(tags=["Quality control"])


@contextmanager
def connect():
    """Use the digital twin's existing connection pool and transaction boundary."""
    raw = sync_engine.raw_connection()
    try:
        with raw.driver_connection.transaction():
            with raw.driver_connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SET LOCAL statement_timeout = '5s'")
                cursor.execute("SET LOCAL lock_timeout = '3s'")
                yield cursor
    except psycopg.Error as error:
        raise HTTPException(503, "QC transaction unavailable; retry with the same inspection ID") from error
    finally:
        raw.close()


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_id: str = Field(min_length=1, max_length=100)
    product_present: bool = False


class Cancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=5, max_length=1000)


class Result(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    detected_variant: Literal["A", "B", "C", "D"] | None = None
    mean_quality_score: float | None = Field(default=None, ge=0, le=100)


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    inspection_id: UUID
    mode: Literal["manual", "order"]
    station_id: str = Field(min_length=1, max_length=100)
    product_instance_id: int | None = Field(default=None, gt=0)
    order_id: int | None = Field(default=None, gt=0)
    arrival_event_id: int | None = Field(default=None, gt=0)
    expected_variant: Literal["A", "B", "C", "D"] | None = None
    completed_at: AwareDatetime
    calibration_id: str = Field(min_length=1, max_length=200)
    profile_name: str
    video_path: str | None = None
    result: Result
    findings: list[dict] = Field(default_factory=list, max_length=200)


LATEST = """
 SELECT e.*, a.asset_key FROM product_tracking_events e
 LEFT JOIN assets a ON a.asset_id=e.asset_id
 WHERE e.product_instance_id=%s
 ORDER BY e.time DESC,e.product_tracking_event_id DESC LIMIT 1
"""


def current_arrival(db, product_id, arrival_id):
    latest = db.execute(LATEST, (product_id,)).fetchone()
    if not latest or latest["product_tracking_event_id"] != arrival_id or latest["state"] != "arrived" or latest["asset_key"] != "visual_qc":
        raise HTTPException(409, "Product is no longer at the claimed QC arrival; inspect tracking before retrying")
    return latest



def claim_direct_arrival(db, station_id):
    """Bind a camera arrival to the next order unit without inventing production."""
    row = db.execute("""
      SELECT p.product_instance_id,i.order_id,m.variant
      FROM product_instances p JOIN order_items i USING(order_item_id)
      JOIN orders o USING(order_id)
      LEFT JOIN qc_variant_mapping m ON m.product_type_id=i.product_type_id
      WHERE o.status IN ('pending','in_progress')
        AND i.completed_quantity < i.requested_quantity
        AND (p.status='queued' OR (p.status IN ('rework','in_progress') AND
          EXISTS (SELECT 1 FROM product_tracking_events e
            WHERE e.product_instance_id=p.product_instance_id
              AND e.external_event_id LIKE 'qc-direct:%%'
              AND e.state='arrived'
              AND e.product_tracking_event_id=(SELECT t.product_tracking_event_id
                FROM product_tracking_events t WHERE t.product_instance_id=p.product_instance_id
                ORDER BY t.time DESC,t.product_tracking_event_id DESC LIMIT 1))))
        AND NOT EXISTS (SELECT 1 FROM tray_product_assignments t
          WHERE t.product_instance_id=p.product_instance_id AND t.released_at IS NULL)
        AND NOT EXISTS (SELECT 1 FROM qc_jobs j
          WHERE j.product_instance_id=p.product_instance_id AND j.finished_at IS NULL)
      ORDER BY o.priority DESC,o.order_date,o.order_id,i.position,p.sequence_number,p.product_instance_id
      LIMIT 1 FOR UPDATE OF p,o
    """).fetchone()
    if not row:
        return None
    if row['variant'] is None:
        raise HTTPException(422, "Product type has no qc_variant_mapping")
    location = db.execute("""SELECT aps.process_step_id,aps.asset_id FROM asset_process_steps aps
      JOIN process_configurations pc USING(process_configuration_id) JOIN assets a USING(asset_id)
      WHERE pc.is_active AND a.asset_key='visual_qc' ORDER BY aps.process_step_id LIMIT 1""").fetchone()
    if not location:
        raise HTTPException(422, "visual_qc not mapped in active process")
    identifier = uuid4()
    arrival = db.execute("""INSERT INTO product_tracking_events
      (product_instance_id,process_step_id,asset_id,state,external_event_id)
      VALUES (%s,%s,%s,'arrived',%s) RETURNING product_tracking_event_id""",
      (row['product_instance_id'],location['process_step_id'],location['asset_id'],
       'qc-direct:'+str(identifier))).fetchone()
    db.execute("UPDATE product_instances SET status='in_progress' WHERE product_instance_id=%s",
               (row['product_instance_id'],))
    return db.execute("""INSERT INTO qc_jobs
      (inspection_id,arrival_event_id,product_instance_id,order_id,expected_variant,station_id)
      VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""", (identifier,
      arrival['product_tracking_event_id'],row['product_instance_id'],row['order_id'],row['variant'],station_id)).fetchone()


@router.post("/qc/claim", dependencies=[Depends(require_api_key)])
def claim(body: Claim):
    with connect() as db:
        # One physical camera area: serialize claims; never guess among multiple arrivals.
        db.execute("SELECT pg_advisory_xact_lock(20480924)")
        job = db.execute("SELECT * FROM qc_jobs WHERE station_id=%s AND finished_at IS NULL", (body.station_id,)).fetchone()
        if job:
            current_arrival(db, job["product_instance_id"], job["arrival_event_id"])
            return job
        if db.execute("SELECT 1 FROM qc_jobs WHERE finished_at IS NULL LIMIT 1").fetchone():
            raise HTTPException(409, "visual_qc is claimed by another station; station IDs must be unique")
        rows = db.execute("""
          SELECT p.product_instance_id, i.order_id, m.variant,
                 e.product_tracking_event_id AS arrival_event_id
          FROM product_instances p JOIN order_items i USING(order_item_id)
          JOIN orders o USING(order_id)
          JOIN LATERAL (SELECT * FROM product_tracking_events t
             WHERE t.product_instance_id=p.product_instance_id
             ORDER BY t.time DESC,t.product_tracking_event_id DESC LIMIT 1) e ON true
          JOIN assets a ON a.asset_id=e.asset_id
          LEFT JOIN qc_variant_mapping m ON m.product_type_id=i.product_type_id
          WHERE p.status IN ('assigned','in_progress','rework')
            AND o.status IN ('pending','in_progress')
            AND e.state='arrived' AND a.asset_key='visual_qc'
            AND NOT EXISTS (SELECT 1 FROM qc_jobs j WHERE j.arrival_event_id=e.product_tracking_event_id)
          ORDER BY e.time,e.product_tracking_event_id LIMIT 2
        """).fetchall()
        if not rows:
            return claim_direct_arrival(db, body.station_id) if body.product_present else None
        if len(rows) != 1:
            raise HTTPException(409, "Multiple products at visual_qc; resolve tracking ambiguity")
        row = rows[0]
        if row["variant"] is None:
            raise HTTPException(422, "Product type has no qc_variant_mapping")
        product = db.execute("SELECT status FROM product_instances WHERE product_instance_id=%s FOR UPDATE", (row["product_instance_id"],)).fetchone()
        if product["status"] not in ("assigned", "in_progress", "rework"):
            raise HTTPException(409, "Product is not available")
        current_arrival(db, row["product_instance_id"], row["arrival_event_id"])
        return db.execute("""INSERT INTO qc_jobs
          (inspection_id,arrival_event_id,product_instance_id,order_id,expected_variant,station_id)
          VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
          (uuid4(), row["arrival_event_id"], row["product_instance_id"], row["order_id"], row["variant"], body.station_id)).fetchone()


def accept(db, body, payload):
    # Serialize retries before lookup; comparison detects accidental UUID reuse.
    db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(body.inspection_id),))
    existing = db.execute("SELECT payload FROM qc_inspections WHERE inspection_id=%s", (body.inspection_id,)).fetchone()
    if existing:
        if existing["payload"] != payload:
            raise HTTPException(409, "Inspection ID already has a different payload")
        return {"inspection_id": str(body.inspection_id), "accepted": True, "duplicate": True}
    if body.mode == "manual":
        if any(value is not None for value in (body.product_instance_id, body.order_id, body.arrival_event_id, body.expected_variant)):
            raise HTTPException(422, "Manual inspection cannot change an order/product")
    else:
        job = db.execute("SELECT * FROM qc_jobs WHERE inspection_id=%s FOR UPDATE", (body.inspection_id,)).fetchone()
        if not job or job["finished_at"] is not None:
            raise HTTPException(409, "No active claim for this inspection")
        for key in ("product_instance_id", "order_id", "arrival_event_id", "expected_variant", "station_id"):
            if getattr(body, key) != job[key]:
                raise HTTPException(409, f"Result does not match claim: {key}")
        product = db.execute("SELECT * FROM product_instances WHERE product_instance_id=%s FOR UPDATE", (body.product_instance_id,)).fetchone()
        if product["status"] not in ("assigned", "in_progress", "rework"):
            raise HTTPException(409, "Product is terminal or unavailable")
        # Serialize completion counts for different products of the same order.
        order = db.execute("SELECT status FROM orders WHERE order_id=%s FOR UPDATE", (body.order_id,)).fetchone()
        if order["status"] not in ("pending", "in_progress"):
            raise HTTPException(409, "Order is no longer active")
        arrival = current_arrival(db, body.product_instance_id, body.arrival_event_id)
        tray = db.execute("SELECT * FROM tray_product_assignments WHERE product_instance_id=%s AND released_at IS NULL FOR UPDATE", (body.product_instance_id,)).fetchone()
        direct = arrival.get('external_event_id') == 'qc-direct:'+str(body.inspection_id)
        if direct and (tray or arrival['tray_id'] is not None):
            raise HTTPException(409, "Direct QC product acquired a tray; inspect tracking")
        if not direct and (not tray or arrival["tray_id"] != tray["tray_id"]):
            raise HTTPException(409, "QC arrival does not match active tray")
        if body.result.status == "PASS" and body.result.detected_variant != body.expected_variant:
            raise HTTPException(422, "PASS requires the expected variant")
    db.execute("""INSERT INTO qc_inspections
      (inspection_id,product_instance_id,order_id,mode,station_id,expected_variant,detected_variant,
       status,quality_score,completed_at,calibration_id,payload)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
      (body.inspection_id,body.product_instance_id,body.order_id,body.mode,body.station_id,
       body.expected_variant,body.result.detected_variant,body.result.status,
       body.result.mean_quality_score,body.completed_at,body.calibration_id,Jsonb(payload)))
    # Derive categories from recorded measurements, not arbitrary submitted labels.
    for finding in findings(payload["result"]):
        db.execute("""INSERT INTO qc_findings (inspection_id,code,category,part,sample_count,metrics)
          VALUES (%s,%s,%s,%s,%s,%s)""", (body.inspection_id,finding["code"],finding["category"],
          finding["part"],finding["sample_count"],Jsonb(finding["metrics"])))
    if body.mode == "order":
        status = body.result.status
        if status == "FAIL":
            db.execute("UPDATE product_instances SET status='rework',completed_at=NULL WHERE product_instance_id=%s", (body.product_instance_id,))
        elif status == "PASS":
            location = db.execute("""SELECT aps.process_step_id,aps.asset_id FROM asset_process_steps aps
              JOIN process_configurations pc USING(process_configuration_id) JOIN assets a USING(asset_id)
              WHERE pc.is_active AND a.asset_key='warehouse' ORDER BY aps.process_step_id DESC LIMIT 1""").fetchone()
            if not location:
                raise HTTPException(422, "Warehouse not mapped in active process")
            # Use the twin database clock (its timestamps are naive), never the Pi clock.
            done_time = db.execute("SELECT GREATEST(localtimestamp,%s::timestamp,%s::timestamp) AS t", (arrival["time"],tray["assigned_at"] if tray else arrival["time"])).fetchone()["t"]
            db.execute("UPDATE product_instances SET status='done',completed_at=%s WHERE product_instance_id=%s", (done_time,body.product_instance_id))
            if tray:
                db.execute("UPDATE tray_product_assignments SET released_at=%s WHERE tray_product_assignment_id=%s", (done_time,tray["tray_product_assignment_id"]))
            db.execute("""INSERT INTO product_tracking_events
              (product_instance_id,process_step_id,asset_id,tray_id,time,state,external_event_id)
              VALUES (%s,%s,%s,%s,%s,'done',%s)""", (body.product_instance_id,location["process_step_id"],
              location["asset_id"],tray["tray_id"] if tray else None,done_time,"qc:"+str(body.inspection_id)))
            db.execute("""UPDATE order_items SET completed_quantity=(SELECT count(*) FROM product_instances
              WHERE order_item_id=%s AND status IN ('done','completed')) WHERE order_item_id=%s""",
              (product["order_item_id"],product["order_item_id"]))
            db.execute("""UPDATE orders SET status=CASE WHEN NOT EXISTS
              (SELECT 1 FROM order_items WHERE order_id=%s AND completed_quantity<requested_quantity)
              THEN 'completed' ELSE 'in_progress' END WHERE order_id=%s""", (body.order_id,body.order_id))
        # INCONCLUSIVE records a measurement problem, never a false product failure.
        db.execute("UPDATE qc_jobs SET finished_at=now() WHERE inspection_id=%s", (body.inspection_id,))
    return {"inspection_id": str(body.inspection_id), "accepted": True, "duplicate": False}


@router.post("/qc/results", dependencies=[Depends(require_api_key)])
def receive(body: Event, idempotency_key: str = Header(default="")):
    if idempotency_key != str(body.inspection_id):
        raise HTTPException(422, "Idempotency-Key must match inspection_id")
    payload = body.model_dump(mode="json", exclude_unset=True)
    try:
        json.dumps(payload, allow_nan=False)
    except ValueError:
        raise HTTPException(422, "Non-finite measurement") from None
    with connect() as db:
        return accept(db, body, payload)


@router.get("/qc/results/{inspection_id}", dependencies=[Depends(require_api_key)])
def get_result(inspection_id: UUID):
    with connect() as db:
        row = db.execute("SELECT payload FROM qc_inspections WHERE inspection_id=%s", (inspection_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Inspection not found")
        return row["payload"]


@router.post("/qc/jobs/{inspection_id}/cancel", dependencies=[Depends(require_mapping_admin_api_key)])
def cancel_job(inspection_id: UUID, body: Cancel):
    """Explicit operator recovery; never turns a cancelled measurement into PASS."""
    with connect() as db:
        job = db.execute("SELECT * FROM qc_jobs WHERE inspection_id=%s FOR UPDATE", (inspection_id,)).fetchone()
        if job is None:
            raise HTTPException(404, "Job not found")
        if job["finished_at"] is not None:
            raise HTTPException(409, "Job is already closed")
        db.execute("UPDATE qc_jobs SET finished_at=now(),cancel_reason=%s WHERE inspection_id=%s", (body.reason,inspection_id))
    return {"cancelled": True, "product_status_changed": False}


class VariantMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variant: Literal["A", "B", "C", "D"]


@router.get("/qc/variants", dependencies=[Depends(require_api_key)])
def list_variants():
    with connect() as db:
        return db.execute("SELECT p.product_type_id,p.product_type_key,p.product_type_name,m.variant "
                          "FROM product_types p LEFT JOIN qc_variant_mapping m USING(product_type_id) "
                          "ORDER BY p.product_type_id").fetchall()


@router.put("/qc/variants/{product_type_id}", dependencies=[Depends(require_mapping_admin_api_key)])
def set_variant(product_type_id: int, body: VariantMapping):
    with connect() as db:
        if not db.execute("SELECT 1 FROM product_types WHERE product_type_id=%s", (product_type_id,)).fetchone():
            raise HTTPException(404, "Product type not found")
        db.execute("INSERT INTO qc_variant_mapping(product_type_id,variant) VALUES(%s,%s) "
                   "ON CONFLICT(product_type_id) DO UPDATE SET variant=EXCLUDED.variant", (product_type_id,body.variant))
    return {"product_type_id": product_type_id, "variant": body.variant}
