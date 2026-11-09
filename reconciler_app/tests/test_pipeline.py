import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from reconciler_app.api.main import create_app
from reconciler_app.core.config import Settings
from reconciler_app.db.connection import make_session_factory
from reconciler_app.db.models import PipelineRun
from reconciler_app.services.ingestion import ingest_file
from reconciler_app.services.pipeline import pipeline_status
from reconciler_app.services.reconciliation import reconcile
from reconciler_app.tests.conftest import FIXTURES
from reconciler_app.tests.test_persistence import MARKETPLACE_FILES, MERCHANT, _load_master_data


@pytest.fixture
def session(db_engine):
    with make_session_factory(db_engine)() as s:
        yield s


def _ingest(session, names):
    return [ingest_file(session, MERCHANT, n, (FIXTURES / n).read_bytes()) for n in names]


def _stages(status):
    return {s["key"]: s for s in status["stages"]}


def test_empty_pipeline_is_not_started(session):
    status = pipeline_status(session, MERCHANT)
    assert {s["state"] for s in status["stages"]} == {"not_started"}
    assert status["reconcile"]["stale"] is False
    assert status["runs"] == []


def test_every_ingest_attempt_is_logged(session):
    _ingest(session, MARKETPLACE_FILES[:1])
    _ingest(session, MARKETPLACE_FILES[:1])  # duplicate
    ingest_file(session, MERCHANT, "junk.csv", b"a,b\n1,2\n")  # failure
    runs = session.scalars(select(PipelineRun).order_by(PipelineRun.started_at)).all()
    assert [(r.stage, r.status) for r in runs] == [
        ("INGEST", "ok"),
        ("INGEST", "skipped_duplicate"),
        ("INGEST", "error"),
    ]
    assert runs[0].rows == 5
    assert runs[0].details["encoding"] == "utf-8-sig"
    assert "Could not identify" in runs[2].message

    status = pipeline_status(session, MERCHANT)
    assert status["ingest_attempts"] == {"ok": 1, "skipped_duplicate": 1, "error": 1}
    assert _stages(status)["ingest"]["state"] == "attention"

    # A successful reconcile clears old upload failures from the stage state.
    reconcile(session, MERCHANT)
    assert _stages(pipeline_status(session, MERCHANT))["ingest"]["state"] == "ok"


def test_full_pipeline_status(session):
    _load_master_data(session)
    _ingest(session, MARKETPLACE_FILES)
    status = pipeline_status(session, MERCHANT)
    assert status["reconcile"]["stale"] is True
    assert _stages(status)["reconcile"]["state"] == "not_started"

    reconcile(session, MERCHANT)
    status = pipeline_status(session, MERCHANT)
    stages = _stages(status)
    assert status["reconcile"]["stale"] is False
    assert stages["reconcile"]["state"] == "ok"
    assert stages["store"]["headline"] == "9 orders"
    # 7 active orders (2 cancelled), 6 of them paid out
    assert status["coverage"]["active_orders"] == 7
    assert status["coverage"]["settled_orders"] == 6
    assert stages["match"]["progress"] == pytest.approx(85.7)
    assert stages["map"]["state"] == "attention"  # LZ-UNKNOWN-9
    assert status["reconcile"]["status_counts"]["UNMAPPED_SKU"] == 1
    shopee = next(c for c in status["channels"] if c["channel"] == "SHOPEE")
    assert shopee == {
        "channel": "SHOPEE",
        "orders": 4,
        "cancelled": 1,
        "items": 5,
        "settled_orders": 2,
        "settlement_rows": 2,
    }
    sources = {s["report_type"]: s for s in status["sources"]}
    assert sources["LAZADA_SETTLEMENT"]["files"] == 1
    assert sources["LAZADA_SETTLEMENT"]["last_ingested_at"].endswith("+07:00")
    assert status["runs"][0]["stage"] == "RECONCILE"
    assert status["runs"][0]["details"]["status_counts"]["HEALTHY"] == 4


def test_new_upload_after_reconcile_marks_results_stale(session):
    _load_master_data(session)
    _ingest(session, MARKETPLACE_FILES[:2])
    reconcile(session, MERCHANT)
    assert pipeline_status(session, MERCHANT)["reconcile"]["stale"] is False
    _ingest(session, MARKETPLACE_FILES[2:3])
    status = pipeline_status(session, MERCHANT)
    assert status["reconcile"]["stale"] is True
    assert status["reconcile"]["files_since_last_run"] == 1
    assert _stages(status)["reconcile"]["state"] == "attention"


def test_payout_without_order_is_reported(session):
    _ingest(session, ["shopee_settlement_en.csv"])
    assert pipeline_status(session, MERCHANT)["coverage"]["orphan_settlements"] == 2


def test_pipeline_endpoint(db_engine):
    app = create_app(Settings(auto_migrate=False), engine=db_engine)
    with TestClient(app) as client:
        res = client.get("/pipeline/status", params={"merchant_id": str(MERCHANT)})
        assert res.status_code == 200
        assert [s["key"] for s in res.json()["stages"]] == ["ingest", "store", "match", "map", "reconcile"]
