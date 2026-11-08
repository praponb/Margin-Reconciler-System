"""CSV / Excel ingestion -> canonical Polars frames."""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from reconciler_app.core import normalize as nz
from reconciler_app.core.canonical_mapping import (
    REPORT_SPECS,
    SETTLEMENT_FEE_FIELDS,
    ReportSpec,
    map_columns,
)
from reconciler_app.core.config import get_settings
from reconciler_app.core.sniffer import SniffResult, UnknownReportError, read_rows, sniff

ORDER_COLUMNS = ("channel_order_id", "order_date", "order_status", "buyer_total_paid")
ITEM_COLUMNS = (
    "channel_order_id",
    "channel_sku",
    "quantity",
    "unit_original_price",
    "seller_discount",
    "subtotal",
)
SETTLEMENT_COLUMNS = ("channel_order_id", "settlement_date", *SETTLEMENT_FEE_FIELDS, "net_payout_amount")


@dataclass
class CanonicalBatch:
    report_type: str
    channel: str
    kind: str
    orders: pl.DataFrame | None = None
    items: pl.DataFrame | None = None
    settlements: pl.DataFrame | None = None
    source_rows: int = 0
    skipped_rows: int = 0
    dropped_columns: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        frame = self.items if self.kind == "ORDER" else self.settlements
        return 0 if frame is None else frame.height


def _dedupe(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for i, h in enumerate(headers):
        name = str(h).strip() or f"column_{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}__{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out


def rows_to_frame(rows: list[list[str]], header_row: int, spec: ReportSpec) -> tuple[pl.DataFrame, list[str]]:
    """Slice off metadata, map headers to canonical tokens, drop unknown columns."""
    headers = _dedupe(rows[header_row])
    width = len(headers)
    body = [(r + [""] * width)[:width] for r in rows[header_row + 1 :] if any(str(c).strip() for c in r)]
    raw = pl.DataFrame(body, schema=dict.fromkeys(headers, pl.Utf8), orient="row")
    mapping, dropped = map_columns(headers, spec)
    df = raw.select([pl.col(h).alias(c) for h, c in mapping.items()])
    return df, dropped


def _parse_order_report(df: pl.DataFrame, tz: str) -> tuple[pl.DataFrame, pl.DataFrame, int]:
    has_qty = "quantity" in df.columns
    has_subtotal = "subtotal" in df.columns
    has_total = "buyer_total_paid" in df.columns
    df = df.with_columns(
        pl.col("channel_order_id").str.strip_chars(),
        pl.col("channel_sku").str.strip_chars(),
        pl.col("order_status").str.strip_chars()
        if "order_status" in df.columns
        else pl.lit("").alias("order_status"),
        nz.integer("quantity") if has_qty else pl.lit(1, dtype=pl.Int64).alias("quantity"),
        nz.money("unit_original_price"),
        nz.money("seller_discount", absolute=True)
        if "seller_discount" in df.columns
        else pl.lit(0.0).alias("seller_discount"),
        nz.money("subtotal") if has_subtotal else pl.lit(None, dtype=pl.Float64).alias("subtotal"),
        nz.money("buyer_total_paid")
        if has_total
        else pl.lit(None, dtype=pl.Float64).alias("buyer_total_paid"),
        nz.parse_datetime("order_date", tz),
    )
    if not has_subtotal:
        df = df.with_columns(
            (pl.col("quantity") * pl.col("unit_original_price") - pl.col("seller_discount")).alias("subtotal")
        )
    before = df.height
    df = df.filter(
        (pl.col("channel_order_id") != "")
        & pl.col("channel_order_id").is_not_null()
        & pl.col("order_date").is_not_null()
    )
    skipped = before - df.height

    items = (
        df.group_by(["channel_order_id", "channel_sku"], maintain_order=True)
        .agg(
            pl.col("quantity").sum(),
            pl.col("unit_original_price").first(),
            pl.col("seller_discount").sum(),
            pl.col("subtotal").sum(),
        )
        .select(ITEM_COLUMNS)
    )
    total = pl.col("buyer_total_paid").first() if has_total else pl.col("subtotal").sum()
    orders = (
        df.group_by("channel_order_id", maintain_order=True)
        .agg(
            pl.col("order_date").first(),
            pl.col("order_status").first(),
            total.alias("buyer_total_paid"),
        )
        .select(ORDER_COLUMNS)
    )
    return orders, items, skipped


def _parse_settlement_report(df: pl.DataFrame, spec: ReportSpec, tz: str) -> tuple[pl.DataFrame, int]:
    df = df.with_columns(
        pl.col("channel_order_id").str.strip_chars(),
        nz.parse_date("settlement_date", tz),
    )
    before = df.height
    df = df.filter(
        (pl.col("channel_order_id") != "")
        & pl.col("channel_order_id").is_not_null()
        & pl.col("settlement_date").is_not_null()
    )
    skipped = before - df.height

    if spec.layout == "long":
        wide = nz.pivot_long_settlement(df.with_columns(nz.money("amount")))
    else:
        exprs = []
        for c in SETTLEMENT_FEE_FIELDS:
            exprs.append(nz.money(c, absolute=True) if c in df.columns else pl.lit(0.0).alias(c))
        exprs.append(
            nz.money("net_payout_amount")
            if "net_payout_amount" in df.columns
            else pl.lit(0.0).alias("net_payout_amount")
        )
        wide = df.with_columns(exprs)

    settlements = (
        wide.group_by(["channel_order_id", "settlement_date"], maintain_order=True)
        .agg(pl.col(c).sum() for c in (*SETTLEMENT_FEE_FIELDS, "net_payout_amount"))
        .select(SETTLEMENT_COLUMNS)
    )
    return settlements, skipped


def parse(
    file_bytes: bytes, filename: str | None = None, sniffed: SniffResult | None = None
) -> CanonicalBatch:
    """Sniff (unless already sniffed) and parse a marketplace export."""
    tz = get_settings().local_timezone
    sniffed = sniffed or sniff(file_bytes, filename)
    if not sniffed.is_known:
        raise UnknownReportError(
            f"Could not identify platform/report type for {filename or 'file'} "
            f"(best signature score {sniffed.score})."
        )
    spec = REPORT_SPECS[sniffed.report_type]
    df, dropped = rows_to_frame(sniffed.rows, sniffed.header_row, spec)
    batch = CanonicalBatch(
        report_type=spec.key,
        channel=spec.channel,
        kind=spec.kind,
        source_rows=df.height,
        dropped_columns=dropped,
    )
    if spec.kind == "ORDER":
        batch.orders, batch.items, batch.skipped_rows = _parse_order_report(df, tz)
    else:
        batch.settlements, batch.skipped_rows = _parse_settlement_report(df, spec, tz)
    return batch


def parse_simple_table(file_bytes: bytes, spec_key: str, filename: str | None = None) -> pl.DataFrame:
    """Parse a product-master or SKU-mapping upload (header on the first non-empty row)."""
    spec = REPORT_SPECS[spec_key]
    rows, _ = read_rows(file_bytes, filename)
    header_row = next((i for i, r in enumerate(rows) if any(str(c).strip() for c in r)), 0)
    df, _ = rows_to_frame(rows, header_row, spec)
    return df.with_columns(pl.col(pl.Utf8).str.strip_chars())
