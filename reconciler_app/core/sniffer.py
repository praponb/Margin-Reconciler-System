"""File decoding, header detection and platform signature matching.

Detection reads the first `SNIFF_LINES` rows. Each row is scored against every
report signature (the number of cells that match a known alias for that report
type). The best-scoring row is taken as the header row, and any metadata lines
above it are skipped. A result is accepted only if it scores at least `MIN_SCORE`
and no other report type ties with it.
"""

from __future__ import annotations

import codecs
import csv
import io
from dataclasses import dataclass, field

import polars as pl

from reconciler_app.core.canonical_mapping import REPORT_SPECS, normalize_header

SNIFF_LINES = 15
MIN_SCORE = 3
ENCODING_CANDIDATES = ("utf-8", "cp874", "tis-620")
UNKNOWN = "UNKNOWN"


class UnknownReportError(ValueError):
    pass


@dataclass(frozen=True)
class SniffResult:
    report_type: str
    channel: str | None
    kind: str | None
    encoding: str | None  # None for spreadsheets
    header_row: int
    score: int
    rows: list[list[str]] = field(repr=False, compare=False, default_factory=list)

    @property
    def is_known(self) -> bool:
        return self.report_type != UNKNOWN


def is_spreadsheet(file_bytes: bytes, filename: str | None = None) -> bool:
    if filename and filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return True
    return file_bytes[:4] == b"PK\x03\x04"


def decode_bytes(file_bytes: bytes) -> tuple[str, str]:
    """Decode text using BOM detection, then strict UTF-8, then Thai code pages."""
    if file_bytes.startswith(codecs.BOM_UTF8):
        return file_bytes.decode("utf-8-sig"), "utf-8-sig"
    if file_bytes.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return file_bytes.decode("utf-16"), "utf-16"
    for encoding in ENCODING_CANDIDATES:
        try:
            return file_bytes.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="replace"), "utf-8"


def read_rows(file_bytes: bytes, filename: str | None = None) -> tuple[list[list[str]], str | None]:
    """Return all rows as lists of strings, plus the text encoding (None for xlsx)."""
    if is_spreadsheet(file_bytes, filename):
        df = pl.read_excel(io.BytesIO(file_bytes), has_header=False, infer_schema_length=0)
        rows = [["" if v is None else str(v) for v in row] for row in df.rows()]
        return rows, None
    text, encoding = decode_bytes(file_bytes)
    sample_lines = text.splitlines()[:SNIFF_LINES]
    # Pick the delimiter with the most occurrences on any single line in the sample.
    # (Metadata lines rarely contain many delimiters; the header row does.)
    delimiter = max(",\t;|", key=lambda d: max((line.count(d) for line in sample_lines), default=0))
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return rows, encoding


def score_rows(rows: list[list[str]]) -> tuple[str, int, int]:
    """Return (report_type, header_row, score) for the best match in the first lines."""
    best: dict[str, tuple[int, int]] = {}  # report_type -> (score, row_idx)
    for idx, row in enumerate(rows[:SNIFF_LINES]):
        cells = {normalize_header(c) for c in row if c and str(c).strip()}
        if not cells:
            continue
        for key, spec in REPORT_SPECS.items():
            if not spec.detectable:
                continue
            score = len(cells & spec.signature)
            if score > best.get(key, (0, -1))[0]:
                best[key] = (score, idx)

    if not best:
        return UNKNOWN, 0, 0
    ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)
    top_key, (top_score, top_row) = ranked[0]
    if top_score < MIN_SCORE:
        return UNKNOWN, 0, top_score
    if len(ranked) > 1 and ranked[1][1][0] == top_score:
        return UNKNOWN, top_row, top_score  # ambiguous
    return top_key, top_row, top_score


def sniff(file_bytes: bytes, filename: str | None = None) -> SniffResult:
    rows, encoding = read_rows(file_bytes, filename)
    report_type, header_row, score = score_rows(rows)
    spec = REPORT_SPECS.get(report_type)
    return SniffResult(
        report_type=report_type,
        channel=spec.channel if spec else None,
        kind=spec.kind if spec else None,
        encoding=encoding,
        header_row=header_row,
        score=score,
        rows=rows,
    )
