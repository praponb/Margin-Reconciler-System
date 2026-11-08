import altair as alt
import polars as pl
import streamlit as st

from reconciler_app.core.config import get_settings
from reconciler_app.ui_common import SERIES_COLOR, api, baht, call, merchant_id

st.title("Pricing simulator")
st.caption("Find the listing price that reaches a target net margin after platform fees and 7% VAT on fees.")

products = call(api().get, "/products", merchant_id=merchant_id()) or []
by_sku = {p["internal_sku"]: p for p in products}

left, right = st.columns([1, 1])
with left:
    sku = st.selectbox("Start from product (optional)", ["—", *by_sku])
    p = by_sku.get(sku, {})
    cogs = st.number_input("Unit COGS (฿)", min_value=0.0, value=float(p.get("cogs_amount", 100.0)), step=1.0)
    packaging = st.number_input(
        "Unit packaging (฿)", min_value=0.0, value=float(p.get("packaging_cost", 5.0))
    )
    shipping = st.number_input("Shipping buffer per unit (฿)", min_value=0.0, value=10.0)
    commission = st.slider("Commission %", 0.0, 30.0, 5.0, 0.1)
    transaction = st.slider("Transaction / payment fee %", 0.0, 10.0, 2.0, 0.1)
    service = st.slider("Service fee %", 0.0, 20.0, 3.0, 0.1)
    target = st.slider("Target net margin %", -20.0, 80.0, 20.0, 0.5)
    vat = st.number_input(
        "VAT on fees", min_value=0.0, max_value=1.0, value=get_settings().vat_rate, step=0.01
    )

body = {
    "cogs": cogs,
    "packaging": packaging,
    "shipping_buffer": shipping,
    "commission_pct": commission,
    "transaction_pct": transaction,
    "service_pct": service,
    "target_margin_pct": target,
    "vat_rate": vat,
}
with right:
    res = call(api().post, "/pricing/recommend", json=body)
    if res:
        st.metric("Recommended listing price", baht(res["recommended_price"]))
        st.caption(f"Effective fee rate incl. VAT: {res['effective_fee_rate'] * 100:.2f}%")
        parts = pl.DataFrame(
            {
                "component": ["Unit cost", "Platform fees", "VAT on fees", "Net profit"],
                "amount": [res["unit_cost"], res["platform_fees"], res["vat_on_fees"], res["net_profit"]],
            }
        )
        chart = (
            alt.Chart(parts.to_pandas())
            .mark_bar(color=SERIES_COLOR, cornerRadiusEnd=4)
            .encode(
                y=alt.Y("component:N", sort=None, title=None, scale=alt.Scale(paddingInner=0.3)),
                x=alt.X("amount:Q", title="฿ per unit"),
                tooltip=[alt.Tooltip("component:N"), alt.Tooltip("amount:Q", format=",.2f")],
            )
            .properties(height=alt.Step(32), title="Where the price goes")
            .configure_axis(grid=False)
        )
        st.altair_chart(chart, width="stretch")
        st.dataframe(parts, hide_index=True, width="stretch")
