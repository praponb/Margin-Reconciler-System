"""Cleaning expressions and settlement normalisation shared by parser and engine."""

from __future__ import annotations

import polars as pl

from reconciler_app.core.canonical_mapping import (
    CANCELLED_STATUS_KEYWORDS,
    LAZADA_FEE_LOOKUP,
    SETTLEMENT_FEE_FIELDS,
    normalize_header,
)

DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d %b %Y %H:%M:%S",
    "%d %b %Y %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d %b %Y",
)

BUDDHIST_ERA_OFFSET = 543


def money(name: str, *, absolute: bool = False) -> pl.Expr:
    """Parse '฿1,234.50', '(12.00)', '-', '' -> Float64, null -> 0.0."""
    s = pl.col(name).cast(pl.Utf8).str.strip_chars()
    negative = s.str.contains(r"^\(.*\)$").fill_null(False)
    cleaned = s.str.replace_all(r"(?i)thb|[฿,\s()]", "")
    value = cleaned.cast(pl.Float64, strict=False)
    value = pl.when(negative).then(-value).otherwise(value).fill_null(0.0).fill_nan(0.0)
    if absolute:
        value = value.abs()
    return value.alias(name)


def integer(name: str, default: int = 1) -> pl.Expr:
    value = money(name).round(0).cast(pl.Int64)
    return pl.when(value > 0).then(value).otherwise(default).alias(name)


def parse_datetime(name: str, tz: str = "Asia/Bangkok") -> pl.Expr:
    """Parse common marketplace formats (day-first); convert Buddhist-Era years."""
    s = pl.col(name).cast(pl.Utf8).str.strip_chars().str.replace(r"\.\d+$", "")
    parsed = pl.coalesce([s.str.strptime(pl.Datetime("us"), fmt, strict=False) for fmt in DATETIME_FORMATS])
    parsed = (
        pl.when(parsed.dt.year() > 2400)
        .then(parsed.dt.offset_by(f"-{BUDDHIST_ERA_OFFSET}y"))
        .otherwise(parsed)
    )
    return parsed.dt.replace_time_zone(tz).alias(name)


def parse_date(name: str, tz: str = "Asia/Bangkok") -> pl.Expr:
    return parse_datetime(name, tz).dt.date().alias(name)


def is_cancelled(status_col: str = "order_status") -> pl.Expr:
    lowered = pl.col(status_col).cast(pl.Utf8).fill_null("").str.to_lowercase()
    return pl.any_horizontal([lowered.str.contains(k, literal=True) for k in CANCELLED_STATUS_KEYWORDS])


def pivot_long_settlement(df: pl.DataFrame) -> pl.DataFrame:
    """Lazada-style fee lines (fee_name, amount) -> one wide row per order/date.

    Export amounts are signed from the seller's view (fees negative). Cost buckets
    are flipped to positive costs; credit buckets keep their sign. Unknown fee
    names with negative amounts count as service_fee.
    """
    bucket = pl.col("fee_name").map_elements(
        lambda n: LAZADA_FEE_LOOKUP.get(normalize_header(n), "unknown"), return_dtype=pl.Utf8
    )
    lines = df.with_columns(bucket.alias("bucket"))
    lines = lines.with_columns(
        pl.when((pl.col("bucket") == "unknown") & (pl.col("amount") < 0))
        .then(pl.lit("service_fee"))
        .otherwise(pl.col("bucket"))
        .alias("bucket")
    )

    def bucket_sum(name: str) -> pl.Expr:
        return pl.col("amount").filter(pl.col("bucket") == name).sum()

    cost_buckets = ("commission_fee", "transaction_fee", "service_fee", "actual_shipping_fee_charged")
    credit_buckets = ("shipping_fee_paid_by_buyer", "voucher_platform_subsidy")
    return (
        lines.group_by(["channel_order_id", "settlement_date"], maintain_order=True)
        .agg(
            *[(0.0 - bucket_sum(b)).alias(b) for b in cost_buckets],  # avoids -0.0
            *[bucket_sum(b).alias(b) for b in credit_buckets],
            pl.col("amount").sum().alias("net_payout_amount"),
        )
        .select("channel_order_id", "settlement_date", *SETTLEMENT_FEE_FIELDS, "net_payout_amount")
    )


def aggregate_settlements(settlements: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    """Collapse several settlement slices (e.g. adjustments) into one row per order."""
    return settlements.group_by(keys).agg(
        *[pl.col(c).fill_null(0.0).sum() for c in (*SETTLEMENT_FEE_FIELDS, "net_payout_amount")],
        pl.col("settlement_date").max(),
    )
