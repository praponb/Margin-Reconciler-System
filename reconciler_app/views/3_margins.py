import altair as alt
import polars as pl
import streamlit as st

from reconciler_app.ui_common import (
    LOSS_COLOR,
    PROFIT_COLOR,
    SERIES_COLOR,
    STATUS_META,
    api,
    baht,
    call,
    filter_bar,
    rows_frame,
    status_label,
)

st.title("Margins")
params = filter_bar("margins")

summary = call(api().get, "/margins/summary", **params)
if not summary:
    st.info("No reconciled data yet. Upload exports and run reconciliation first.")
    st.stop()

s = rows_frame(summary)
revenue = s["gross_revenue"].sum()
profit = s["net_profit"].sum()
fees = s["total_fees"].sum()
gap = s["shipping_gap"].sum()

k = st.columns(5)
k[0].metric("Gross revenue", baht(revenue))
k[1].metric("Net profit", baht(profit))
k[2].metric("Net margin", f"{(profit / revenue * 100) if revenue else 0:.1f}%")
k[3].metric("Platform fees", baht(fees))
k[4].metric("Shipping overcharge", baht(gap))

# Status tiles (icon + label + count; colour never carries meaning alone)
lines = call(api().get, "/margins", **params, limit=5000) or {"rows": []}
detail = rows_frame(lines["rows"])
if detail.height:
    counts = dict(detail.group_by("reconcile_status").len().iter_rows())
    cols = st.columns(len(STATUS_META))
    for col, code in zip(cols, STATUS_META, strict=True):
        col.metric(status_label(code), counts.get(code, 0))

st.subheader("Net profit by SKU and channel")
chart_df = s.with_columns(
    pl.concat_str("internal_sku", "channel", separator=" · ").alias("sku_channel"),
    pl.when(pl.col("net_profit") < 0).then(pl.lit("Loss")).otherwise(pl.lit("Profit")).alias("result"),
).to_pandas()
bars = (
    alt.Chart(chart_df)
    .mark_bar(cornerRadiusEnd=4)
    .encode(
        y=alt.Y(
            "sku_channel:N",
            sort="x",
            title=None,
            scale=alt.Scale(paddingInner=0.3),
            axis=alt.Axis(labelLimit=260),
        ),
        x=alt.X("net_profit:Q", title="Net profit (฿)"),
        color=alt.Color(
            "result:N",
            scale=alt.Scale(domain=["Profit", "Loss"], range=[PROFIT_COLOR, LOSS_COLOR]),
            legend=alt.Legend(title=None, orient="top"),
        ),
        tooltip=[
            alt.Tooltip("internal_sku:N", title="SKU"),
            alt.Tooltip("channel:N", title="Channel"),
            alt.Tooltip("quantity:Q", title="Units"),
            alt.Tooltip("gross_revenue:Q", title="Revenue ฿", format=",.2f"),
            alt.Tooltip("net_profit:Q", title="Net profit ฿", format=",.2f"),
            alt.Tooltip("net_margin_pct:Q", title="Margin %", format=".2f"),
        ],
    )
)
zero = alt.Chart().mark_rule(color="#8a8984", strokeWidth=1).encode(x=alt.datum(0))
st.altair_chart(
    (bars + zero).properties(height=alt.Step(30)).configure_axis(grid=False),
    width="stretch",
)

st.subheader("P&L table")
st.dataframe(
    s.select(
        "channel",
        "internal_sku",
        "quantity",
        "gross_revenue",
        "total_cogs",
        "total_packaging",
        "total_fees",
        "shipping_gap",
        "net_profit",
        "net_margin_pct",
    ),
    hide_index=True,
    width="stretch",
    column_config={
        c: st.column_config.NumberColumn(format="%.2f")
        for c in (
            "gross_revenue",
            "total_cogs",
            "total_packaging",
            "total_fees",
            "shipping_gap",
            "net_profit",
            "net_margin_pct",
        )
    },
)

if detail.height:
    st.subheader("Margin distribution (order lines with revenue)")
    hist_df = detail.filter(pl.col("gross_revenue") > 0).select("net_margin_pct").to_pandas()
    hist = (
        alt.Chart(hist_df)
        .mark_bar(color=SERIES_COLOR, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, binSpacing=2)
        .encode(
            x=alt.X("net_margin_pct:Q", bin=alt.Bin(step=10), title="Net margin %"),
            y=alt.Y("count():Q", title="Order lines"),
            tooltip=[alt.Tooltip("count():Q", title="Lines")],
        )
        .properties(height=220)
        .configure_axis(grid=False)
    )
    st.altair_chart(hist, width="stretch")
