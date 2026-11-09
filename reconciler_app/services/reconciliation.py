"""Rebuild the fact_sku_margins slice for a merchant and (local) date range."""

from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from reconciler_app.core.engine import STATUSES, reconcile_orders
from reconciler_app.db import repositories as repo


@dataclass
class ReconcileResult:
    rows: int
    status_counts: dict[str, int]
    date_from: date | None
    date_to: date | None


def reconcile(
    session: Session, merchant_id: uuid.UUID, date_from: date | None = None, date_to: date | None = None
) -> ReconcileResult:
    """Rebuild the mart slice and log the run (success or failure) in pipeline_runs."""
    started, t0 = datetime.now(UTC), time.perf_counter()
    scope = {"date_from": date_from and date_from.isoformat(), "date_to": date_to and date_to.isoformat()}

    def log(status: str, rows: int = 0, message: str | None = None, counts: dict | None = None) -> None:
        repo.record_run(
            session,
            merchant_id=merchant_id,
            stage="RECONCILE",
            status=status,
            started_at=started,
            finished_at=datetime.now(UTC),
            duration_ms=int((time.perf_counter() - t0) * 1000),
            rows=rows,
            message=message,
            details={**scope, "status_counts": counts or {}},
        )

    try:
        start, end = repo.local_day_bounds(date_from, date_to)
        inputs = repo.load_engine_inputs(session, merchant_id, start, end)
        margins = reconcile_orders(**inputs)
        rows = repo.replace_margins(session, merchant_id, start, end, margins)
        counts = Counter(margins["reconcile_status"].to_list())
        status_counts = {s: counts.get(s, 0) for s in STATUSES}
        log("ok", rows, counts=status_counts)
        session.commit()
    except Exception as exc:
        session.rollback()
        log("error", message=str(exc))
        session.commit()
        raise
    return ReconcileResult(rows=rows, status_counts=status_counts, date_from=date_from, date_to=date_to)
