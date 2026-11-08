import codecs

import pytest
from fastapi.testclient import TestClient

from reconciler_app.api.main import create_app
from reconciler_app.core.config import Settings
from reconciler_app.tests.conftest import FIXTURES
from reconciler_app.tests.test_persistence import MARKETPLACE_FILES

MID = "00000000-0000-0000-0000-000000000001"
Q = {"merchant_id": MID}


@pytest.fixture
def client(db_engine):
    app = create_app(Settings(auto_migrate=False), engine=db_engine)
    with TestClient(app) as c:
        yield c


def _upload(client, names):
    files = [("files", (n, (FIXTURES / n).read_bytes())) for n in names]
    return client.post("/ingest", data=Q, files=files)


def _bootstrap(client):
    with open(FIXTURES / "products.csv", "rb") as f:
        assert client.post("/products/upload", params=Q, files={"file": f}).json() == {"upserted": 4}
    with open(FIXTURES / "sku_mappings.csv", "rb") as f:
        assert client.post("/sku-mappings/upload", params=Q, files={"file": f}).json() == {"upserted": 7}
    assert all(r["status"] == "ingested" for r in _upload(client, MARKETPLACE_FILES).json())
    return client.post("/reconcile", json={"merchant_id": MID}).json()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_full_flow(client):
    result = _bootstrap(client)
    assert result["rows"] == 11
    assert result["status_counts"]["SHIPPING_OVERCHARGE"] == 2

    page = client.get("/margins", params={**Q, "limit": 5}).json()
    assert page["total"] == 11 and len(page["rows"]) == 5
    assert page["rows"][0]["order_date"].endswith("+07:00")

    exc = client.get("/exceptions", params=Q).json()
    assert exc["total"] == 7
    assert {r["reconcile_status"] for r in exc["rows"]} == {
        "UNMAPPED_SKU",
        "PENDING_PAYOUT",
        "LOSS_MAKING",
        "SHIPPING_OVERCHARGE",
    }
    assert exc["rows"][0]["net_profit"] == min(r["net_profit"] for r in exc["rows"])

    only_loss = client.get("/exceptions", params={**Q, "status": "LOSS_MAKING"}).json()
    assert only_loss["total"] == 3

    shopee = client.get("/margins", params={**Q, "channel": "shopee"}).json()
    assert shopee["total"] == 4

    summary = client.get("/margins/summary", params=Q).json()
    serum = next(r for r in summary if r["internal_sku"] == "SERUM-003")
    assert serum["net_profit"] == -81.0
    assert serum["net_margin_pct"] == pytest.approx(-40.7, abs=0.01)


def test_reupload_reports_duplicates(client):
    _upload(client, MARKETPLACE_FILES[:1])
    again = _upload(client, MARKETPLACE_FILES[:1]).json()
    assert again[0]["skipped_duplicate"] is True
    assert len(client.get("/ingest/batches", params=Q).json()) == 1


def test_unknown_file_is_reported_per_file(client):
    files = [
        ("files", ("junk.csv", b"a,b\n1,2\n")),
        ("files", ("tiktok_orders_en.csv", (FIXTURES / "tiktok_orders_en.csv").read_bytes())),
    ]
    out = client.post("/ingest", data=Q, files=files).json()
    assert [r["status"] for r in out] == ["error", "ingested"]


def test_csv_export_has_bom_and_thai_safe(client):
    _bootstrap(client)
    res = client.get("/margins/export.csv", params={**Q, "exceptions_only": True})
    assert res.status_code == 200
    assert res.content.startswith(codecs.BOM_UTF8)
    lines = res.content.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("reconcile_id,channel,channel_order_id")
    assert len(lines) == 1 + 7


def test_mapping_to_unknown_product_is_422(client):
    res = client.post(
        "/sku-mappings", params=Q, json=[{"channel": "shopee", "channel_sku": "X", "internal_sku": "NOPE"}]
    )
    assert res.status_code == 422


def test_invalid_channel_is_422(client):
    res = client.post(
        "/sku-mappings", params=Q, json=[{"channel": "AMAZON", "channel_sku": "X", "internal_sku": "A"}]
    )
    assert res.status_code == 422


def test_pricing_endpoint(client):
    body = {
        "cogs": 100,
        "packaging": 5,
        "shipping_buffer": 10,
        "commission_pct": 5,
        "transaction_pct": 2,
        "service_pct": 3,
        "target_margin_pct": 20,
    }
    res = client.post("/pricing/recommend", json=body).json()
    assert res["recommended_price"] == 165.9452
    bad = client.post("/pricing/recommend", json={**body, "target_margin_pct": 95})
    assert bad.status_code == 422
    assert "exceed 100%" in bad.json()["detail"]


def test_reconcile_rejects_inverted_range(client):
    res = client.post(
        "/reconcile", json={"merchant_id": MID, "date_from": "2026-11-05", "date_to": "2026-11-01"}
    )
    assert res.status_code == 422
