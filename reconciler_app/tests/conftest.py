import os
from pathlib import Path

import pytest

from reconciler_app.db.connection import make_engine
from reconciler_app.db.models import Base

FIXTURES = Path(__file__).parent / "fixtures"

# Set TEST_DATABASE_URL (e.g. postgresql+psycopg://...) to run DB tests against Postgres.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite://")


@pytest.fixture
def fixture_bytes():
    def _read(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return _read


@pytest.fixture
def db_engine():
    engine = make_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()
