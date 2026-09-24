"""Run with TEST_DATABASE_URL pointing ONLY to an isolated disposable DB.

Each test uses a random schema and removes only that schema on completion.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy import create_engine
from fastapi.testclient import TestClient

from fastapi import FastAPI
from app.qc.api import router
from app.models import Base, QcFinding, QcInspection, QcJob, QcVariantMapping
from app.settings import settings
from pydantic import SecretStr

app = FastAPI()
app.include_router(router)

QC_TABLES = [
    QcVariantMapping.__table__,
    QcJob.__table__,
    QcInspection.__table__,
    QcFinding.__table__,
]


def install_qc_tables(url: str, schema: str):
    engine = create_engine(
        url.replace("postgresql://", "postgresql+psycopg://"),
        connect_args={"options": f"-c search_path={schema}"},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE product_instances "
            "DROP CONSTRAINT IF EXISTS ck_product_instances_status"
        )
        connection.exec_driver_sql(
            "ALTER TABLE product_instances ADD CONSTRAINT "
            "ck_product_instances_status CHECK (status IN "
            "('queued','assigned','in_progress','completed','cancelled','done','rework'))"
        )
        Base.metadata.create_all(connection, tables=QC_TABLES, checkfirst=True)
    return engine

FIXTURE = """
 CREATE TABLE product_types(product_type_id bigint PRIMARY KEY,product_type_key text,product_type_name text);
 CREATE TABLE orders(order_id bigint PRIMARY KEY,status text NOT NULL);
 CREATE TABLE order_items(order_item_id bigint PRIMARY KEY,order_id bigint REFERENCES orders,
 product_type_id bigint REFERENCES product_types,requested_quantity int,completed_quantity int DEFAULT 0);
 CREATE TABLE product_instances(product_instance_id bigint PRIMARY KEY,order_item_id bigint REFERENCES order_items,
 status text NOT NULL,completed_at timestamp,
 CONSTRAINT ck_product_instances_status CHECK(status IN ('queued','assigned','in_progress','completed','cancelled')));
 CREATE TABLE assets(asset_id bigint PRIMARY KEY,asset_key text UNIQUE);
 CREATE TABLE process_configurations(process_configuration_id bigint PRIMARY KEY,is_active boolean);
 CREATE TABLE asset_process_steps(process_configuration_id bigint,process_step_id bigint,asset_id bigint);
 CREATE TABLE tray_product_assignments(tray_product_assignment_id bigint PRIMARY KEY,tray_id bigint,
 product_instance_id bigint REFERENCES product_instances,assigned_at timestamp DEFAULT localtimestamp,released_at timestamp);
 CREATE TABLE product_tracking_events(product_tracking_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 product_instance_id bigint REFERENCES product_instances,process_step_id bigint,asset_id bigint REFERENCES assets,
 tray_id bigint,time timestamp DEFAULT localtimestamp,state text,external_event_id text UNIQUE);
 INSERT INTO product_types VALUES(1,'A','Cat A');
 INSERT INTO orders VALUES(1,'in_progress');
 INSERT INTO order_items(order_item_id,order_id,product_type_id,requested_quantity) VALUES(1,1,1,1);
 INSERT INTO product_instances VALUES(1,1,'in_progress',NULL);
 INSERT INTO assets VALUES(1,'visual_qc'),(2,'warehouse'),(3,'assembly1');
 INSERT INTO process_configurations VALUES(1,true);
 INSERT INTO asset_process_steps VALUES(1,10,1),(1,20,2);
 INSERT INTO tray_product_assignments(tray_product_assignment_id,tray_id,product_instance_id) VALUES(1,1,1);
 INSERT INTO product_tracking_events(product_instance_id,process_step_id,asset_id,tray_id,state) VALUES(1,10,1,1,'arrived');
"""


class AuthTests(unittest.TestCase):
    def test_authentication_fails_closed(self):
        with patch.object(settings, "INBOUND_API_KEY", SecretStr("secret")):
            client = TestClient(app)
            self.assertEqual(client.post("/qc/claim",json={"station_id":"x"}).status_code,401)
            self.assertEqual(client.post("/qc/claim",headers={"X-API-Key":"wrong"},json={"station_id":"x"}).status_code,401)


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL for PostgreSQL integration tests")
class QCApiTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        self.schema = "qc_test_" + uuid4().hex
        with psycopg.connect(self.url, autocommit=True) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        with self.connect() as db:
            db.execute(FIXTURE)
        engine = install_qc_tables(self.url, self.schema)
        engine.dispose()
        with self.connect() as db:
            db.execute("INSERT INTO qc_variant_mapping VALUES(1,'A')")
        self.patcher = patch("app.qc.api.connect", self.connect)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.env = patch.object(settings, "INBOUND_API_KEY", SecretStr("test-key"))
        self.env.start()
        self.addCleanup(self.env.stop)
        admin = patch.object(settings, "MAPPING_ADMIN_API_KEY", SecretStr("test-key"))
        admin.start()
        self.addCleanup(admin.stop)
        self.client = TestClient(app, headers={"X-API-Key": "test-key"})

    def connect(self):
        return psycopg.connect(self.url, row_factory=dict_row, options=f"-c search_path={self.schema} -c statement_timeout=5000")

    def cleanup_schema(self):
        with psycopg.connect(self.url, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def query(self, query):
        with self.connect() as db:
            return db.execute(query).fetchone()

    def claim(self, station="qc-1"):
        response = self.client.post("/qc/claim", json={"station_id": station})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def event(self, status="PASS"):
        job = self.claim()
        return {"schema_version": 1,"inspection_id":job["inspection_id"],"mode":"order",
                "station_id":"qc-1","product_instance_id":1,"order_id":1,
                "arrival_event_id":job["arrival_event_id"],"expected_variant":"A",
                "completed_at":datetime.now(timezone.utc).isoformat(),"calibration_id":"test", "profile_name":"cat",
                "result":{"status":status,"detected_variant":"A","mean_quality_score":95,
                          "failure_counts":{} if status=="PASS" else {"head:shape":4}}}

    def post(self, event):
        return self.client.post("/qc/results", json=event, headers={"Idempotency-Key":event["inspection_id"]})

    def test_pass_is_atomic_and_duplicate_is_harmless(self):
        event = self.event()
        self.assertEqual(self.post(event).status_code, 200)
        self.assertTrue(self.post(event).json()["duplicate"])
        self.assertEqual(self.query("SELECT status FROM product_instances")["status"], "done")
        self.assertEqual(self.query("SELECT completed_quantity FROM order_items")["completed_quantity"], 1)
        self.assertEqual(self.query("SELECT status FROM orders")["status"], "completed")
        self.assertIsNotNone(self.query("SELECT released_at FROM tray_product_assignments")["released_at"])
        self.assertEqual(self.query("SELECT count(*) AS n FROM product_tracking_events WHERE state='done'")["n"], 1)
        self.assertIsNone(self.claim())

    def test_rework_then_new_arrival_can_pass(self):
        event = self.event("FAIL")
        self.assertEqual(self.post(event).status_code, 200)
        self.assertEqual(self.query("SELECT status FROM product_instances")["status"], "rework")
        self.assertIsNone(self.query("SELECT released_at FROM tray_product_assignments")["released_at"])
        self.assertEqual(self.query("SELECT category FROM qc_findings")["category"], "shape")
        self.assertIsNone(self.claim())
        with self.connect() as db:
            db.execute("INSERT INTO product_tracking_events(product_instance_id,asset_id,tray_id,state) VALUES(1,1,1,'arrived')")
        self.assertEqual(self.post(self.event()).status_code, 200)
        self.assertEqual(self.query("SELECT count(*) AS n FROM qc_inspections")["n"], 2)

    def test_inconclusive_keeps_product_state(self):
        self.assertEqual(self.post(self.event("INCONCLUSIVE")).status_code, 200)
        self.assertEqual(self.query("SELECT status FROM product_instances")["status"], "in_progress")

    def test_stale_arrival_rejected_without_partial_writes(self):
        event = self.event()
        with self.connect() as db:
            db.execute("INSERT INTO product_tracking_events(product_instance_id,asset_id,tray_id,state) VALUES(1,3,1,'arrived')")
        self.assertEqual(self.post(event).status_code, 409)
        self.assertEqual(self.query("SELECT count(*) AS n FROM qc_inspections")["n"], 0)

    def test_wrong_variant_cannot_pass(self):
        event = self.event()
        event["result"]["detected_variant"] = "B"
        self.assertEqual(self.post(event).status_code, 422)

    def test_manual_does_not_change_product(self):
        event = self.event()
        event.update(mode="manual", product_instance_id=None,order_id=None,arrival_event_id=None,expected_variant=None,inspection_id=str(uuid4()))
        self.assertEqual(self.post(event).status_code, 200)
        self.assertEqual(self.query("SELECT status FROM product_instances")["status"], "in_progress")

    def test_missing_warehouse_rolls_back_every_write(self):
        event = self.event()
        with self.connect() as db:
            db.execute("DELETE FROM asset_process_steps WHERE asset_id=2")
        self.assertEqual(self.post(event).status_code, 422)
        self.assertEqual(self.query("SELECT count(*) AS n FROM qc_inspections")["n"], 0)
        self.assertEqual(self.query("SELECT status FROM product_instances")["status"], "in_progress")

    def test_concurrent_duplicate_and_conflicting_retry(self):
        event = self.event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.post(event), range(2)))
        self.assertEqual([r.status_code for r in responses], [200,200])
        self.assertEqual(sorted(r.json()["duplicate"] for r in responses), [False,True])
        event["result"]["mean_quality_score"] = 70
        self.assertEqual(self.post(event).status_code, 409)

    def test_claim_survives_restart_and_missing_mapping_rejected(self):
        first = self.claim()
        self.assertEqual(first["inspection_id"], self.claim()["inspection_id"])
        self.assertEqual(self.client.post("/qc/claim",json={"station_id":"another-station"}).status_code,409)

    def test_explicit_cancel_is_audited_and_does_not_change_product(self):
        job = self.claim()
        response = self.client.post(f"/qc/jobs/{job['inspection_id']}/cancel",json={"reason":"Operator removed tray"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.query("SELECT cancel_reason FROM qc_jobs")["cancel_reason"],"Operator removed tray")
        self.assertEqual(self.query("SELECT status FROM product_instances")["status"],"in_progress")
        self.assertIsNone(self.claim())

    def test_no_mapping_is_not_guessed(self):
        with self.connect() as db:
            db.execute("DELETE FROM qc_variant_mapping")
        self.assertEqual(self.client.post("/qc/claim",json={"station_id":"qc-1"}).status_code,422)


    def test_central_variant_mapping_api(self):
        response = self.client.put("/qc/variants/1",json={"variant":"B"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get("/qc/variants").json()[0]["variant"],"B")
        self.assertEqual(self.claim()["expected_variant"],"B")

    def test_shared_pool_and_central_metadata(self):
        engine = install_qc_tables(self.url, self.schema)
        self.patcher.stop()
        try:
            with patch("app.qc.api.sync_engine",engine):
                event = self.event()
                self.assertEqual(self.post(event).status_code,200)
                self.assertTrue(self.post(event).json()["duplicate"])
            self.assertEqual(self.query("SELECT status FROM product_instances")["status"],"done")
        finally:
            engine.dispose()

    def test_admin_actions_reject_station_key(self):
        with patch.object(settings,"MAPPING_ADMIN_API_KEY",SecretStr("admin-only")):
            response = self.client.put("/qc/variants/1",json={"variant":"B"})
            self.assertEqual(response.status_code,401)
