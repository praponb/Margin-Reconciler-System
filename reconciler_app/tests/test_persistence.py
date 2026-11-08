import uuid
from datetime import date
from decimal import Decimal

import polars as pl
import pytest
from sqlalchemy import func, select

from reconciler_app.core.parser import parse_simple_table
from reconciler_app.db import repositories as repo
from reconciler_app.db.connection import make_engine, make_session_factory, run_migrations
from reconciler_app.db.models import FactOrder, FactOrderItem, FactSettlement, FactSkuMargin
from reconciler_app.services.ingestion import ingest_file
from reconciler_app.services.reconciliation import reconcile
from reconciler_app.tests.conftest import FIXTURES

MERCHANT = uuid.UUID("00000000-0000-0000-0000-000000000001")
MARKETPLACE_FILES = [
    "shopee_orders_th.csv",
    "shopee_settlement_en.csv",
    "lazada_orders_en.xlsx",
    "lazada_settlement_th_cp874.csv",
    "tiktok_orders_en.csv",
    "tiktok_settlement_th.xlsx",
]


@pytest.fixture
def session(db_engine):
    with make_session_factory(db_engine)() as s:
        yield s


def _load_master_data(session):
    products = parse_simple_table((FIXTURES / "products.csv").read_bytes(), "PRODUCT_MASTER")
    repo.upsert_products(session, MERCHANT, products.to_dicts())
    mappings = parse_simple_table((FIXTURES / "sku_mappings.csv").read_bytes(), "SKU_MAPPING")
    repo.upsert_mappings(session, MERCHANT, mappings.to_dicts())
    session.commit()


def _ingest_all(session):
    return [ingest_file(session, MERCHANT, n, (FIXTURES / n).read_bytes()) for n in MARKETPLACE_FILES]


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_ingest_all_fixtures(session):
    results = _ingest_all(session)
    assert [r.status for r in results] == ["ingested"] * len(MARKETPLACE_FILES), [r.message for r in results]
    assert _count(session, FactOrder) == 9
    assert _count(session, FactOrderItem) == 13
    assert _count(session, FactSettlement) == 6


def test_reingesting_same_file_is_a_noop(session):
    _ingest_all(session)
    before = (_count(session, FactOrder), _count(session, FactOrderItem), _count(session, FactSettlement))
    again = _ingest_all(session)
    assert all(r.skipped_duplicate for r in again)
    after = (_count(session, FactOrder), _count(session, FactOrderItem), _count(session, FactSettlement))
    assert before == after


def test_updated_export_replaces_orders_instead_of_duplicating(session):
    data = (FIXTURES / "tiktok_orders_en.csv").read_bytes()
    ingest_file(session, MERCHANT, "a.csv", data)
    ingest_file(session, MERCHANT, "b.csv", data.replace(b"5701,Completed", b"5701,Delivered"))
    statuses = dict(session.execute(select(FactOrder.channel_order_id, FactOrder.order_status)).all())
    assert statuses["5701"] == "Delivered"
    assert _count(session, FactOrder) == 3


def test_bad_file_is_reported_and_rolled_back(session):
    result = ingest_file(session, MERCHANT, "junk.csv", b"a,b\n1,2\n")
    assert result.status == "error"
    assert "Could not identify" in result.message
    assert _count(session, FactOrder) == 0


def test_reconcile_end_to_end_and_rerun_is_stable(session):
    _load_master_data(session)
    _ingest_all(session)
    first = reconcile(session, MERCHANT)
    assert first.rows == 11
    assert first.status_counts == {
        "UNMAPPED_SKU": 1,
        "PENDING_PAYOUT": 1,
        "LOSS_MAKING": 3,
        "SHIPPING_OVERCHARGE": 2,
        "HEALTHY": 4,
    }
    second = reconcile(session, MERCHANT)
    assert second.rows == first.rows
    assert _count(session, FactSkuMargin) == first.rows

    row = session.scalar(
        select(FactSkuMargin).where(
            FactSkuMargin.channel_order_id == "2611010001", FactSkuMargin.internal_sku == "TSHIRT-001"
        )
    )
    assert row.allocated_commission == Decimal("15.0000")
    assert row.allocated_shipping_gap == Decimal("15.0000")
    assert row.net_profit == Decimal("85.0000")


def test_reconcile_date_range_only_replaces_that_slice(session):
    _load_master_data(session)
    _ingest_all(session)
    reconcile(session, MERCHANT)
    # Bangkok-local 1 Nov only: Shopee 2611010001 (2 lines)
    result = reconcile(session, MERCHANT, date(2026, 11, 1), date(2026, 11, 1))
    assert result.rows == 2
    assert _count(session, FactSkuMargin) == 11


def test_mapping_requires_known_product(session):
    with pytest.raises(ValueError, match="Unknown internal SKU"):
        repo.upsert_mappings(
            session, MERCHANT, [{"channel": "SHOPEE", "channel_sku": "X", "internal_sku": "NOPE"}]
        )


def test_alembic_migration_builds_schema(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    run_migrations(url)
    engine = make_engine(url)
    with make_session_factory(engine)() as s:
        repo.upsert_products(
            s, MERCHANT, [{"internal_sku": "A", "product_name": "a", "cogs_amount": 1.0, "packaging_cost": 0}]
        )
        s.commit()
        assert len(repo.list_products(s, MERCHANT)) == 1
    engine.dispose()


def test_round_trip_frames_have_engine_schema(session):
    _load_master_data(session)
    _ingest_all(session)
    inputs = repo.load_engine_inputs(session, MERCHANT, None, None)
    assert inputs["orders"]["order_date"].dtype == pl.Datetime("us", "UTC")
    assert inputs["items"].height == 13
