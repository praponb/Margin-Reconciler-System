"""Data pipeline status: what each stage has processed, coverage, freshness and run history."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reconciler_app.core.canonical_mapping import CANCELLED_STATUS_KEYWORDS, CHANNELS
from reconciler_app.core.config import get_settings
from reconciler_app.core.engine import STATUSES
from reconciler_app.db import repositories as repo
from reconciler_app.db.models import (
    FactOrder,
    FactOrderItem,
    FactSettlement,
    FactSkuMargin,
    IngestionBatch,
    PipelineRun,
)

REPORT_TYPES = [f"{c}_{k}" for c in CHANNELS for k in ("ORDER", "SETTLEMENT")]

# Stage states shown on the page
OK, ATTENTION, PENDING, ERROR = "ok", "attention", "not_started", "error"


def _local(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return repo.as_utc(dt).astimezone(ZoneInfo(get_settings().local_timezone)).isoformat()


def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _is_cancelled(status: str | None) -> bool:
    s = (status or "").lower()
    return any(k in s for k in CANCELLED_STATUS_KEYWORDS)


def _run_dict(r: PipelineRun) -> dict:
    return {
        "run_id": str(r.run_id),
        "stage": r.stage,
        "status": r.status,
        "started_at": _local(r.started_at),
        "finished_at": _local(r.finished_at),
        "duration_ms": r.duration_ms,
        "filename": r.filename,
        "report_type": r.report_type,
        "rows": r.rows,
        "message": r.message,
        "details": r.details or {},
    }


def pipeline_status(session: Session, merchant_id: uuid.UUID, history: int = 50) -> dict:
    mid = merchant_id

    # --- 1. Ingest: files per report type, plus attempts by outcome --------------------------
    sources = {
        rt: {"report_type": rt, "files": 0, "rows": 0, "last_ingested_at": None} for rt in REPORT_TYPES
    }
    last_ingest = None
    for rt, files, rows, last in session.execute(
        select(
            IngestionBatch.report_type,
            func.count(),
            func.sum(IngestionBatch.row_count),
            func.max(IngestionBatch.ingested_at),
        )
        .where(IngestionBatch.merchant_id == mid)
        .group_by(IngestionBatch.report_type)
    ):
        sources[rt] = {
            "report_type": rt,
            "files": files,
            "rows": int(rows or 0),
            "last_ingested_at": _local(last),
        }
        last_ingest = max(filter(None, [last_ingest, repo.as_utc(last)]), default=None)

    attempts = Counter(
        dict(
            session.execute(
                select(PipelineRun.status, func.count())
                .where(PipelineRun.merchant_id == mid, PipelineRun.stage == "INGEST")
                .group_by(PipelineRun.status)
            ).all()
        )
    )
    files_ingested = sum(s["files"] for s in sources.values())

    # --- 2. Store: orders, items, settlements per channel --------------------------------------
    def blank(channel: str) -> dict:
        return {
            "channel": channel,
            "orders": 0,
            "cancelled": 0,
            "items": 0,
            "settled_orders": 0,
            "settlement_rows": 0,
        }

    per_channel = {c: blank(c) for c in CHANNELS}
    orders = session.execute(
        select(FactOrder.channel, FactOrder.channel_order_id, FactOrder.order_status).where(
            FactOrder.merchant_id == mid
        )
    ).all()
    active: set[tuple[str, str]] = set()
    for channel, oid, status in orders:
        per_channel.setdefault(channel, blank(channel))
        per_channel[channel]["orders"] += 1
        if _is_cancelled(status):
            per_channel[channel]["cancelled"] += 1
        else:
            active.add((channel, oid))
    for channel, n in session.execute(
        select(FactOrder.channel, func.count())
        .join(FactOrderItem, FactOrderItem.order_id == FactOrder.order_id)
        .where(FactOrder.merchant_id == mid)
        .group_by(FactOrder.channel)
    ):
        per_channel.setdefault(channel, blank(channel))["items"] = n

    # --- 3. Match settlements to orders ---------------------------------------------------------
    settled_keys: set[tuple[str, str]] = set()
    settlement_rows = defaultdict(int)
    for channel, oid in session.execute(
        select(FactSettlement.channel, FactSettlement.channel_order_id).where(
            FactSettlement.merchant_id == mid
        )
    ):
        settled_keys.add((channel, oid))
        settlement_rows[channel] += 1
    order_keys = {(c, o) for c, o, _ in orders}
    for channel, n in settlement_rows.items():
        per_channel.setdefault(channel, blank(channel))
        per_channel[channel]["settlement_rows"] = n
    for channel, _oid in active & settled_keys:
        per_channel[channel]["settled_orders"] += 1
    orphan_settlements = len(settled_keys - order_keys)
    settled_active = len(active & settled_keys)

    # --- 4/5. Reconcile: mart contents and last run ---------------------------------------------
    mart_counts = dict(
        session.execute(
            select(FactSkuMargin.reconcile_status, func.count())
            .where(FactSkuMargin.merchant_id == mid)
            .group_by(FactSkuMargin.reconcile_status)
        ).all()
    )
    mart_rows = sum(mart_counts.values())
    unmapped = mart_counts.get("UNMAPPED_SKU", 0)

    last_ok = session.scalar(
        select(PipelineRun)
        .where(PipelineRun.merchant_id == mid, PipelineRun.stage == "RECONCILE", PipelineRun.status == "ok")
        .order_by(PipelineRun.started_at.desc())
        .limit(1)
    )
    files_since = 0
    if last_ok:
        # Compare against the run log: both timestamps are written by the app in the same format.
        files_since = (
            session.scalar(
                select(func.count())
                .select_from(PipelineRun)
                .where(
                    PipelineRun.merchant_id == mid,
                    PipelineRun.stage == "INGEST",
                    PipelineRun.status == "ok",
                    PipelineRun.started_at > last_ok.started_at,
                )
            )
            or 0
        )
    # Without a logged reconcile run (e.g. data reconciled before run logging existed) we can't
    # prove the results are current, so ask for a re-run.
    stale = bool(files_ingested) and (last_ok is None or files_since > 0)

    failure_filter = [
        PipelineRun.merchant_id == mid,
        PipelineRun.stage == "INGEST",
        PipelineRun.status == "error",
    ]
    if last_ok:
        failure_filter.append(PipelineRun.started_at > last_ok.started_at)
    recent_failures = (
        session.scalar(select(func.count()).select_from(PipelineRun).where(*failure_filter)) or 0
    )

    runs = repo.list_runs(session, mid, history)
    last_error = next((r for r in runs if r.status == "error"), None)

    # --- Stage cards ---------------------------------------------------------------------------
    n_orders = len(orders)
    stages = [
        {
            "key": "ingest",
            "title": "Ingest files",
            "state": PENDING if not files_ingested else (ATTENTION if recent_failures else OK),
            "headline": f"{files_ingested} files",
            "detail": f"{attempts.get('skipped_duplicate', 0)} duplicates skipped · "
            f"{recent_failures} failed since last reconcile",
        },
        {
            "key": "store",
            "title": "Parse & store",
            "state": OK if n_orders else PENDING,
            "headline": f"{n_orders:,} orders",
            "detail": f"{sum(c['items'] for c in per_channel.values()):,} order lines · "
            f"{sum(settlement_rows.values()):,} settlement rows",
        },
        {
            "key": "match",
            "title": "Match payouts",
            "state": PENDING if not active else (OK if settled_active == len(active) else ATTENTION),
            "headline": f"{_pct(settled_active, len(active))}% settled",
            "detail": f"{settled_active:,} of {len(active):,} active orders paid out · "
            f"{orphan_settlements} payouts without an order",
            "progress": _pct(settled_active, len(active)),
        },
        {
            "key": "map",
            "title": "Map SKUs",
            "state": PENDING if not mart_rows else (OK if unmapped == 0 else ATTENTION),
            "headline": f"{_pct(mart_rows - unmapped, mart_rows)}% mapped",
            "detail": f"{unmapped} order lines with an unmapped SKU",
            "progress": _pct(mart_rows - unmapped, mart_rows),
        },
        {
            "key": "reconcile",
            "title": "Reconcile",
            "state": PENDING if not mart_rows else (ATTENTION if stale else OK),
            "headline": f"{mart_rows:,} lines",
            "detail": (
                "Not run yet"
                if not mart_rows
                else "No reconcile run recorded yet: run it to confirm results are current"
                if last_ok is None
                else f"Needs a re-run: {files_since} file(s) uploaded since the last reconcile"
                if stale
                else "Up to date"
            ),
        },
    ]
    if last_error and last_error.stage == "RECONCILE" and runs and runs[0].run_id == last_error.run_id:
        stages[-1]["state"] = ERROR
        stages[-1]["detail"] = f"Last run failed: {last_error.message}"

    return {
        "generated_at": _local(datetime.now(UTC)),
        "stages": stages,
        "sources": list(sources.values()),
        "channels": list(per_channel.values()),
        "coverage": {
            "active_orders": len(active),
            "settled_orders": settled_active,
            "orphan_settlements": orphan_settlements,
            "mart_rows": mart_rows,
            "unmapped_lines": unmapped,
        },
        "reconcile": {
            "last_run": _run_dict(last_ok) if last_ok else None,
            "last_ingest_at": _local(last_ingest),
            "files_since_last_run": files_since,
            "stale": stale,
            "status_counts": {s: mart_counts.get(s, 0) for s in STATUSES},
        },
        "ingest_attempts": {k: attempts.get(k, 0) for k in ("ok", "skipped_duplicate", "error")},
        "runs": [_run_dict(r) for r in runs],
    }
