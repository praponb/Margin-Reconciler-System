"""Streamlit dashboard entrypoint.

Run the API first:   uv run uvicorn reconciler_app.api.main:app
Then:                uv run streamlit run reconciler_app/app.py
"""

from __future__ import annotations

import uuid

import streamlit as st

from reconciler_app.core.config import get_settings
from reconciler_app.ui_client import ApiClient

st.set_page_config(page_title="Margin Reconciler", page_icon="📊", layout="wide")

settings = get_settings()


@st.cache_resource
def _client(base_url: str) -> ApiClient:
    return ApiClient(base_url)


with st.sidebar:
    st.markdown("### Margin Reconciler")
    api_url = st.text_input("API URL", value=settings.api_base_url)
    merchant = st.text_input("Merchant ID", value=settings.default_merchant_id, key="merchant_input")
    try:
        st.session_state["merchant_id"] = str(uuid.UUID(merchant.strip()))
    except ValueError:
        st.error("Merchant ID must be a UUID.")
        st.stop()
    client = _client(api_url)
    st.session_state["api"] = client
    health = client.health()
    if health:
        st.caption(f"✅ API connected · {health['database']}")
    else:
        st.caption("⚠️ API not reachable. Start it with `uv run uvicorn reconciler_app.api.main:app`.")

nav = st.navigation(
    [
        st.Page("views/1_upload.py", title="Upload & reconcile", icon="📥", default=True),
        st.Page("views/2_products.py", title="Products & SKU mapping", icon="🏷️"),
        st.Page("views/3_margins.py", title="Margins", icon="📈"),
        st.Page("views/4_exceptions.py", title="Exceptions queue", icon="🚩"),
        st.Page("views/5_pricing.py", title="Pricing simulator", icon="🧮"),
    ]
)
nav.run()
