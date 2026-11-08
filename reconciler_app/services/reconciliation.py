"""Rebuild the fact_sku_margins slice for a merchant and (local) date range."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date

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
    start, end = repo.local_day_bounds(date_from, date_to)
    inputs = repo.load_engine_inputs(session, merchant_id, start, end)
    margins = reconcile_orders(**inputs)
    try:
        rows = repo.replace_margins(session, merchant_id, start, end, margins)
        session.commit()
    except Exception:
        session.rollback()
        raise
    counts = Counter(margins["reconcile_status"].to_list())
    return ReconcileResult(
        rows=rows,
        status_counts={s: counts.get(s, 0) for s in STATUSES},
        date_from=date_from,
        date_to=date_to,
    )
