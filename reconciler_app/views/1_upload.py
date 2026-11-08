import polars as pl
import streamlit as st

from reconciler_app.ui_common import api, call, merchant_id, status_label

st.title("Upload & reconcile")
st.caption(
    "Drop Shopee, Lazada or TikTok Shop order and settlement exports (CSV or XLSX, Thai or English). "
    "The platform and report type are detected automatically. Re-uploading an identical file does nothing."
)

files = st.file_uploader(
    "Marketplace exports", type=["csv", "xlsx", "xls", "txt"], accept_multiple_files=True
)
if st.button("Ingest files", type="primary", disabled=not files):
    payload = [("files", (f.name, f.getvalue())) for f in files]
    results = call(api().post, "/ingest", data={"merchant_id": merchant_id()}, files=payload)
    if results:
        for r in results:
            if r["status"] == "ingested":
                st.success(f"**{r['filename']}** → {r['report_type']} · {r['rows']} rows")
            elif r["status"] == "skipped_duplicate":
                st.info(f"**{r['filename']}** was already ingested — skipped.")
            else:
                st.error(f"**{r['filename']}**: {r['message']}")
        st.dataframe(
            pl.DataFrame(results).select(
                "filename",
                "status",
                "report_type",
                "encoding",
                "header_row",
                "rows",
                "skipped_rows",
                pl.col("dropped_columns").list.join(", "),
            ),
            hide_index=True,
            width="stretch",
        )

st.divider()
st.subheader("Run reconciliation")
c1, c2 = st.columns([2, 1])
dates = c1.date_input("Order date range (Bangkok time; leave empty for all orders)", value=())
body = {"merchant_id": merchant_id()}
if isinstance(dates, tuple) and len(dates) == 2:
    body["date_from"], body["date_to"] = dates[0].isoformat(), dates[1].isoformat()
if c2.button("Reconcile", type="primary", width="stretch"):
    res = call(api().post, "/reconcile", json=body)
    if res:
        st.success(f"Rebuilt {res['rows']} SKU margin lines.")
        cols = st.columns(len(res["status_counts"]))
        for col, (code, n) in zip(cols, res["status_counts"].items(), strict=True):
            col.metric(status_label(code), n)

st.divider()
st.subheader("Recent uploads")
batches = call(api().get, "/ingest/batches", merchant_id=merchant_id())
if batches:
    st.dataframe(pl.DataFrame(batches), hide_index=True, width="stretch")
else:
    st.caption("No files ingested yet.")
