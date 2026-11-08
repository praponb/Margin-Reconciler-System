"""FastAPI application entrypoint.

Run:  uv run uvicorn reconciler_app.api.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine, text

from reconciler_app.api.routes import ingest, margins, pricing, products, reconcile
from reconciler_app.core.config import Settings, get_settings
from reconciler_app.db.connection import make_engine, make_session_factory, run_migrations


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = engine or make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.auto_migrate:
            run_migrations(settings.database_url)
        yield
        engine.dispose()

    app = FastAPI(
        title="Multi-Channel Fee & Margin Reconciler",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    @app.get("/health", tags=["ops"])
    def health():
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": engine.dialect.name}

    for module in (ingest, products, reconcile, margins, pricing):
        app.include_router(module.router)
    return app


app = create_app()
