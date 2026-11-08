import polars as pl
import streamlit as st

from reconciler_app.ui_common import api, call, filter_bar, merchant_id, status_label

st.title("Exceptions queue")
st.caption("Order lines that need attention, worst profit first. Times are shown in Bangkok time.")
params = filter_bar("exceptions", with_status=True)

page_size = 200
page = st.number_input("Page", min_value=1, value=1, step=1)
res = call(api().get, "/exceptions", **params, limit=page_size, offset=(page - 1) * page_size)
if res is None:
    st.stop()
if not res["rows"]:
    st.success("No exceptions for these filters. 🎉")
    st.stop()

st.caption(f"{res['total']} exception lines")
df = pl.DataFrame(res["rows"]).with_columns(
    pl.col("reconcile_status").map_elements(status_label, return_dtype=pl.Utf8).alias("status"),
    pl.col("order_date").str.slice(0, 16).str.replace("T", " ").alias("order_date (BKK)"),
)
st.dataframe(
    df.select(
        "status",
        "channel",
        "channel_order_id",
        "internal_sku",
        "order_date (BKK)",
        "settlement_date",
        "quantity",
        "gross_revenue",
        "total_cogs",
        "allocated_commission",
        "allocated_service_fee",
        "allocated_transaction",
        "allocated_shipping_gap",
        "net_profit",
        "net_margin_pct",
    ),
    hide_index=True,
    width="stretch",
)

export_params = {k: v for k, v in params.items() if v}
csv_bytes = call(api().download, "/margins/export.csv", **export_params, exceptions_only=True)
if csv_bytes:
    st.download_button(
        "Download CSV (Excel-ready, UTF-8 BOM)",
        data=csv_bytes,
        file_name=f"exceptions_{merchant_id()[:8]}.csv",
        mime="text/csv",
    )
