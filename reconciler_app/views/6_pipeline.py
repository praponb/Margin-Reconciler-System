import polars as pl
import streamlit as st

from reconciler_app.ui_common import api, call, merchant_id, rows_frame, status_label

STATE_META = {
    "ok": ("✅", "Done"),
    "attention": ("⚠️", "Needs attention"),
    "not_started": ("⏳", "Not started"),
    "error": ("❌", "Failed"),
}
RUN_STATUS = {"ok": "✅ OK", "skipped_duplicate": "⏭️ Duplicate skipped", "error": "❌ Failed"}


def bkk(ts: str | None) -> str:
    """ISO timestamp (already Bangkok time from the API) -> 'YYYY-MM-DD HH:MM:SS'."""
    return ts[:19].replace("T", " ") if ts else "—"


def duration(ms: int | None) -> str:
    if ms is None:
        return "—"
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


st.title("Data pipeline")
st.caption(
    "Follow your data from uploaded export files to reconciled margins: what each step has processed, "
    "what still needs attention, and the history of every run. All times are Bangkok time."
)

top = st.columns([3, 1, 1])
auto = top[1].toggle("Auto-refresh (10 s)", value=False)
if top[2].button("Refresh", width="stretch"):
    st.rerun()


@st.fragment(run_every="10s" if auto else None)
def render() -> None:
    data = call(api().get, "/pipeline/status", merchant_id=merchant_id(), history=200)
    if not data:
        return
    rec = data["reconcile"]
    st.caption(f"Status as of {bkk(data['generated_at'])}")

    # --- Freshness banner + reconcile action -------------------------------------------------
    last = rec["last_run"]
    banner, action = st.columns([4, 1])
    if rec["stale"]:
        reason = (
            f"{rec['files_since_last_run']} file(s) ingested since the last reconcile "
            f"({bkk(last['finished_at'])})."
            if last
            else "No reconcile run has been recorded yet."
        )
        banner.warning(f"**Margins may be out of date.** {reason}")
    elif last:
        banner.success(
            f"**Up to date.** Last reconciled {bkk(last['finished_at'])} · "
            f"{last['rows']:,} lines in {duration(last['duration_ms'])}."
        )
    else:
        banner.info("Upload exports on **Upload & reconcile** to start the pipeline.")
    if action.button("Reconcile now", type="primary", width="stretch"):
        with st.status("Reconciling all orders…", expanded=False) as status:
            res = call(api().post, "/reconcile", json={"merchant_id": merchant_id()})
            if res:
                status.update(label=f"Reconciled {res['rows']:,} order lines", state="complete")
            else:
                status.update(label="Reconcile failed", state="error")
        st.rerun(scope="fragment")

    # --- Stage cards -------------------------------------------------------------------------
    st.subheader("Pipeline stages")
    cols = st.columns(len(data["stages"]))
    for i, (col, stage) in enumerate(zip(cols, data["stages"], strict=True), start=1):
        icon, label = STATE_META.get(stage["state"], ("", stage["state"]))
        with col.container(border=True):
            st.markdown(f"**{i}. {stage['title']}**")
            st.markdown(f"### {stage['headline']}")
            st.caption(f"{icon} {label}")
            if "progress" in stage:
                st.progress(min(stage["progress"], 100.0) / 100.0)
            st.caption(stage["detail"])

    # --- Reconciled results ----------------------------------------------------------------------
    counts = rec["status_counts"]
    if sum(counts.values()):
        st.subheader("Reconciled order lines by status")
        status_cols = st.columns(len(counts))
        for col, (code, n) in zip(status_cols, counts.items(), strict=True):
            col.metric(status_label(code), f"{n:,}")

    # --- Sources & channels ----------------------------------------------------------------------
    left, right = st.columns(2)
    with left:
        st.subheader("Source files")
        sources = rows_frame(data["sources"]).with_columns(
            pl.col("report_type").str.replace("_", " · ").alias("report"),
            pl.when(pl.col("files") > 0)
            .then(pl.col("last_ingested_at").map_elements(bkk, return_dtype=pl.Utf8))
            .otherwise(pl.lit("No file yet"))
            .alias("last upload"),
        )
        st.dataframe(
            sources.select("report", "files", "rows", "last upload"), hide_index=True, width="stretch"
        )
    with right:
        st.subheader("By channel")
        channels = rows_frame(data["channels"]).with_columns(
            (pl.col("orders") - pl.col("cancelled")).alias("active"),
        )
        channels = channels.with_columns(
            pl.when(pl.col("active") > 0)
            .then((pl.col("settled_orders") / pl.col("active") * 100).round(1))
            .otherwise(0.0)
            .alias("settled %")
        )
        st.dataframe(
            channels.select(
                "channel", "orders", "cancelled", "items", "settled_orders", "settled %", "settlement_rows"
            ).rename(
                {"items": "order lines", "settled_orders": "paid out", "settlement_rows": "payout rows"}
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "settled %": st.column_config.ProgressColumn(
                    "settled %", min_value=0, max_value=100, format="%.1f%%"
                )
            },
        )
    cov = data["coverage"]
    if cov["orphan_settlements"]:
        st.info(
            f"{cov['orphan_settlements']} payout(s) don't match any uploaded order. "
            "Upload the matching order report so their fees can be allocated."
        )

    # --- Run history ---------------------------------------------------------------------------
    st.subheader("Run history")
    attempts = data["ingest_attempts"]
    st.caption(
        f"File uploads: {attempts['ok']} ingested · {attempts['skipped_duplicate']} duplicates skipped · "
        f"{attempts['error']} failed. Runs from before this page existed are not in the history."
    )
    runs = data["runs"]
    if not runs:
        st.caption("No runs recorded yet. Uploads and reconciles will appear here.")
        return
    f1, f2 = st.columns([1, 1])
    stage_filter = f1.selectbox("Stage", ["All", "INGEST", "RECONCILE"], key="pipeline_stage")
    errors_only = f2.toggle("Failures only", key="pipeline_errors")
    hist = rows_frame(
        [
            {
                "started (BKK)": bkk(r["started_at"]),
                "stage": "📥 Ingest" if r["stage"] == "INGEST" else "🔄 Reconcile",
                "_stage": r["stage"],
                "result": RUN_STATUS.get(r["status"], r["status"]),
                "_status": r["status"],
                "file": r["filename"] or "—",
                "report": (r["report_type"] or "—").replace("_", " · "),
                "rows": r["rows"],
                "duration": duration(r["duration_ms"]),
                "message": r["message"] or "",
            }
            for r in runs
        ]
    )
    if stage_filter != "All":
        hist = hist.filter(pl.col("_stage") == stage_filter)
    if errors_only:
        hist = hist.filter(pl.col("_status") == "error")
    st.dataframe(hist.drop("_stage", "_status"), hide_index=True, width="stretch")


render()
