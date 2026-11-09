"""Helpers shared by the Streamlit pages."""

from __future__ import annotations

import polars as pl
import streamlit as st

from reconciler_app.core.config import get_settings
from reconciler_app.ui_client import ApiClient, ApiError

STATUS_META = {
    "HEALTHY": ("✅", "Healthy"),
    "SHIPPING_OVERCHARGE": ("🚚", "Shipping overcharge"),
    "LOSS_MAKING": ("🔻", "Loss-making"),
    "PENDING_PAYOUT": ("⏳", "Pending payout"),
    "UNMAPPED_SKU": ("❓", "Unmapped SKU"),
}
CHANNELS = ["SHOPEE", "LAZADA", "TIKTOK"]

# Diverging pair (profit vs loss) and single-series hue from the dataviz reference palette.
PROFIT_COLOR = "#2a78d6"
LOSS_COLOR = "#e34948"
SERIES_COLOR = "#2a78d6"


def api() -> ApiClient:
    if "api" not in st.session_state:
        st.session_state["api"] = ApiClient(get_settings().api_base_url)
    return st.session_state["api"]


def merchant_id() -> str:
    return st.session_state.get("merchant_id") or get_settings().default_merchant_id


def call(fn, *args, **kwargs):
    """Run an API call; show the error in the page instead of crashing."""
    try:
        return fn(*args, **kwargs)
    except ApiError as exc:
        st.error(str(exc))
        return None


def status_label(code: str) -> str:
    icon, label = STATUS_META.get(code, ("", code))
    return f"{icon} {label}"


def baht(value: float) -> str:
    return f"฿{value:,.2f}"


def rows_frame(rows: list[dict]) -> pl.DataFrame:
    """API rows -> DataFrame, inferring types from *all* rows.

    Polars infers from the first 100 rows by default; a column that is empty there
    (e.g. settlement_date on pending payouts) would otherwise fail on a later value.
    """
    return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()


def filter_bar(key: str, *, with_status: bool = False) -> dict:
    """One row of filters above the content."""
    cols = st.columns(4 if with_status else 3)
    dates = cols[0].date_input("Order date range", value=(), key=f"{key}_dates")
    channel = cols[1].selectbox("Channel", ["All", *CHANNELS], key=f"{key}_channel")
    sku = cols[2].text_input("SKU contains", key=f"{key}_sku")
    params = {"merchant_id": merchant_id(), "sku": sku or None}
    if channel != "All":
        params["channel"] = channel
    if isinstance(dates, tuple) and len(dates) == 2:
        params["date_from"], params["date_to"] = dates[0].isoformat(), dates[1].isoformat()
    if with_status:
        statuses = [s for s in STATUS_META if s != "HEALTHY"]
        chosen = cols[3].multiselect("Status", statuses, format_func=status_label, key=f"{key}_status")
        if chosen:
            params["status"] = chosen
    return params
