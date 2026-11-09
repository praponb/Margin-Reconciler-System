"""Render every Streamlit page against an in-process API loaded with mock data.

The mock set is large enough that the first 100 margin rows (newest first) are
pending payouts with no settlement date, which used to crash the Margins page
(Polars inferred the column type from those rows only).
"""

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from reconciler_app.api.main import create_app
from reconciler_app.core.config import Settings
from reconciler_app.db.connection import make_engine, make_session_factory
from reconciler_app.db.models import Base
from reconciler_app.services.ingestion import ingest_file
from reconciler_app.services.reconciliation import reconcile
from reconciler_app.tests import make_mock_data
from reconciler_app.tests.conftest import TEST_DATABASE_URL
from reconciler_app.tests.test_persistence import MERCHANT
from reconciler_app.ui_client import ApiClient

PAGES = [
    "views/1_upload.py",
    "views/2_products.py",
    "views/3_margins.py",
    "views/4_exceptions.py",
    "views/5_pricing.py",
    "views/6_pipeline.py",
]


@pytest.fixture(scope="module")
def loaded_client(tmp_path_factory):
    out = tmp_path_factory.mktemp("mock")
    make_mock_data.generate(1000, seed=42, out=out)
    # Module-scoped engine (the shared db_engine fixture is function-scoped).
    engine = make_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app = create_app(Settings(auto_migrate=False), engine=engine)
    client = TestClient(app)
    q = {"merchant_id": str(MERCHANT)}
    for path, name in (
        ("/products/upload", "products_mock.csv"),
        ("/sku-mappings/upload", "sku_mappings_mock.csv"),
    ):
        client.post(path, params=q, files={"file": (name, (out / name).read_bytes())}).raise_for_status()
    with make_session_factory(engine)() as session:
        for f in sorted(out.glob("*_mock.*")):
            if f.stem.split("_")[0] in ("shopee", "lazada", "tiktok"):
                assert ingest_file(session, MERCHANT, f.name, f.read_bytes()).status == "ingested"
        reconcile(session, MERCHANT)
    yield client
    client.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_dataset_covers_the_null_prefix_case(loaded_client):
    rows = loaded_client.get("/margins", params={"merchant_id": str(MERCHANT), "limit": 5000}).json()["rows"]
    assert all(r["settlement_date"] is None for r in rows[:100])
    assert any(r["settlement_date"] is not None for r in rows[100:])


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_errors(loaded_client, page):
    at = AppTest.from_file(f"../{page}", default_timeout=30)
    at.session_state["api"] = ApiClient("http://testserver", client=loaded_client)
    at.session_state["merchant_id"] = str(MERCHANT)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
