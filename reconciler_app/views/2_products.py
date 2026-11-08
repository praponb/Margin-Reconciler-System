import polars as pl
import streamlit as st

from reconciler_app.ui_common import CHANNELS, api, call, merchant_id

st.title("Products & SKU mapping")

tab_products, tab_mappings, tab_unmapped = st.tabs(["Products (COGS)", "SKU mappings", "Unmapped SKUs"])

with tab_products:
    st.caption(
        "Unit COGS and packaging cost per internal SKU. Edit the table and save, or upload a CSV/XLSX."
    )
    products = call(api().get, "/products", merchant_id=merchant_id()) or []
    base = (
        pl.DataFrame(products).select(
            "internal_sku",
            "product_name",
            pl.col("cogs_amount").cast(pl.Float64),
            pl.col("packaging_cost").cast(pl.Float64),
        )
        if products
        else pl.DataFrame(
            schema={
                "internal_sku": pl.Utf8,
                "product_name": pl.Utf8,
                "cogs_amount": pl.Float64,
                "packaging_cost": pl.Float64,
            }
        )
    )
    edited = st.data_editor(
        base.to_pandas(),
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        column_config={
            "cogs_amount": st.column_config.NumberColumn("COGS (฿)", min_value=0.0, format="%.4f"),
            "packaging_cost": st.column_config.NumberColumn("Packaging (฿)", min_value=0.0, format="%.4f"),
        },
        key="products_editor",
    )
    if st.button("Save products", type="primary"):
        rows = [
            {**r, "packaging_cost": r.get("packaging_cost") or 0.0}
            for r in edited.to_dict("records")
            if r.get("internal_sku") and r.get("product_name") and r.get("cogs_amount") is not None
        ]
        if call(api().post, "/products", params={"merchant_id": merchant_id()}, json=rows):
            st.success(f"Saved {len(rows)} products.")
            st.rerun()
    up = st.file_uploader(
        "Bulk upload (columns: SKU, Product Name, COGS, Packaging — Thai headers OK)",
        type=["csv", "xlsx"],
        key="products_upload",
    )
    if up and st.button("Upload products"):
        res = call(
            api().post,
            "/products/upload",
            params={"merchant_id": merchant_id()},
            files={"file": (up.name, up.getvalue())},
        )
        if res:
            st.success(f"Upserted {res['upserted']} products.")

with tab_mappings:
    st.caption(
        "Map each marketplace SKU to an internal SKU. A marketplace SKU identical to an internal SKU "
        "is matched automatically."
    )
    mappings = call(api().get, "/sku-mappings", merchant_id=merchant_id()) or []
    if mappings:
        st.dataframe(pl.DataFrame(mappings).drop("mapping_id"), hide_index=True, width="stretch")
    up = st.file_uploader(
        "Bulk upload (columns: Channel, Channel SKU, Internal SKU)",
        type=["csv", "xlsx"],
        key="mappings_upload",
    )
    if up and st.button("Upload mappings"):
        res = call(
            api().post,
            "/sku-mappings/upload",
            params={"merchant_id": merchant_id()},
            files={"file": (up.name, up.getvalue())},
        )
        if res:
            st.success(f"Upserted {res['upserted']} mappings.")

with tab_unmapped:
    st.caption("Order lines whose SKU matched no product. After mapping them, run reconciliation again.")
    exc = call(api().get, "/exceptions", merchant_id=merchant_id(), status=["UNMAPPED_SKU"], limit=5000)
    rows = (exc or {}).get("rows", [])
    if not rows:
        st.success("Every reconciled order line is mapped.")
    else:
        unmapped = (
            pl.DataFrame(rows)
            .group_by("channel", "internal_sku")
            .agg(pl.len().alias("lines"), pl.col("gross_revenue").sum().alias("revenue"))
            .rename({"internal_sku": "channel_sku"})
            .sort("revenue", descending=True)
        )
        st.dataframe(unmapped, hide_index=True, width="stretch")
        product_skus = [p["internal_sku"] for p in products]
        with st.form("quick_map"):
            choice = st.selectbox(
                "Marketplace SKU",
                unmapped.select(pl.concat_str("channel", "channel_sku", separator=" · "))
                .to_series()
                .to_list(),
            )
            target = st.selectbox("Internal SKU", product_skus)
            if st.form_submit_button("Save mapping", type="primary") and choice and target:
                channel, channel_sku = choice.split(" · ", 1)
                body = [{"channel": channel, "channel_sku": channel_sku, "internal_sku": target}]
                if call(api().post, "/sku-mappings", params={"merchant_id": merchant_id()}, json=body):
                    st.success(
                        f"Mapped {channel} {channel_sku} → {target}. "
                        "Run reconciliation again to update margins."
                    )

st.caption(f"Supported channels: {', '.join(CHANNELS)}")
