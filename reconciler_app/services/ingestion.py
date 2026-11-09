"""Idempotent file ingestion: hash check -> sniff -> parse -> upsert, in one transaction."""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from reconciler_app.core.parser import parse
from reconciler_app.core.sniffer import sniff
from reconciler_app.db import repositories as repo


@dataclass
class IngestResult:
    filename: str
    status: str  # "ingested" | "skipped_duplicate" | "error"
    report_type: str | None = None
    channel: str | None = None
    kind: str | None = None
    encoding: str | None = None
    header_row: int | None = None
    rows: int = 0
    skipped_rows: int = 0
    dropped_columns: list[str] = field(default_factory=list)
    batch_id: str | None = None
    message: str | None = None

    @property
    def skipped_duplicate(self) -> bool:
        return self.status == "skipped_duplicate"

    def as_dict(self) -> dict:
        return {**asdict(self), "skipped_duplicate": self.skipped_duplicate}


def _log_run(
    session: Session, merchant_id: uuid.UUID, result: IngestResult, started: datetime, t0: float
) -> None:
    repo.record_run(
        session,
        merchant_id=merchant_id,
        stage="INGEST",
        status="ok" if result.status == "ingested" else result.status,
        started_at=started,
        finished_at=datetime.now(UTC),
        duration_ms=int((time.perf_counter() - t0) * 1000),
        filename=result.filename[:255],
        report_type=result.report_type,
        rows=result.rows,
        message=result.message,
        details={
            "channel": result.channel,
            "encoding": result.encoding,
            "header_row": result.header_row,
            "skipped_rows": result.skipped_rows,
            "dropped_columns": result.dropped_columns,
        },
    )


def ingest_file(session: Session, merchant_id: uuid.UUID, filename: str, data: bytes) -> IngestResult:
    """Ingest one export. Commits on success and rolls back on any error.

    Every attempt (ingested, duplicate or failed) is logged in pipeline_runs.
    """
    started, t0 = datetime.now(UTC), time.perf_counter()
    digest = hashlib.sha256(data).hexdigest()
    existing = repo.find_batch(session, merchant_id, digest)
    if existing is not None:
        result = IngestResult(
            filename=filename,
            status="skipped_duplicate",
            report_type=existing.report_type,
            channel=existing.channel,
            rows=existing.row_count,
            batch_id=str(existing.batch_id),
            message="Identical file was already ingested; nothing changed.",
        )
        _log_run(session, merchant_id, result, started, t0)
        session.commit()
        return result

    result = IngestResult(filename=filename, status="error")
    try:
        sniffed = sniff(data, filename)
        result.report_type = sniffed.report_type
        result.channel, result.kind = sniffed.channel, sniffed.kind
        result.encoding, result.header_row = sniffed.encoding, sniffed.header_row
        batch = parse(data, filename, sniffed)
        result.dropped_columns = batch.dropped_columns
        result.skipped_rows = batch.skipped_rows

        if batch.kind == "ORDER":
            result.rows = repo.replace_orders(session, merchant_id, batch.channel, batch.orders, batch.items)
        else:
            result.rows = repo.replace_settlements(session, merchant_id, batch.channel, batch.settlements)

        record = repo.record_batch(
            session,
            merchant_id=merchant_id,
            file_sha256=digest,
            filename=filename[:255],
            channel=batch.channel,
            report_type=batch.report_type,
            row_count=result.rows,
        )
        result.status = "ingested"
        result.batch_id = str(record.batch_id)
        _log_run(session, merchant_id, result, started, t0)
        session.commit()
    except Exception as exc:  # report per-file errors; never leave a half-written batch
        session.rollback()
        result.status = "error"
        result.batch_id = None
        result.message = str(exc)
        _log_run(session, merchant_id, result, started, t0)
        session.commit()
    return result
