"""Idempotent file ingestion: hash check -> sniff -> parse -> upsert, in one transaction."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field

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


def ingest_file(session: Session, merchant_id: uuid.UUID, filename: str, data: bytes) -> IngestResult:
    """Ingest one export. Commits on success and rolls back on any error."""
    digest = hashlib.sha256(data).hexdigest()
    existing = repo.find_batch(session, merchant_id, digest)
    if existing is not None:
        return IngestResult(
            filename=filename,
            status="skipped_duplicate",
            report_type=existing.report_type,
            channel=existing.channel,
            rows=existing.row_count,
            batch_id=str(existing.batch_id),
            message="Identical file was already ingested; nothing changed.",
        )

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
        session.commit()
        result.status = "ingested"
        result.batch_id = str(record.batch_id)
    except Exception as exc:  # report per-file errors; never leave a half-written batch
        session.rollback()
        result.message = str(exc)
    return result
