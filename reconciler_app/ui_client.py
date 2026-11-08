"""Thin HTTP client used by the Streamlit UI to talk to the FastAPI backend."""

from __future__ import annotations

from typing import Any

import httpx


class ApiError(RuntimeError):
    pass


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            res = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(f"Cannot reach API at {self.base_url}: {exc}") from exc
        if res.status_code >= 400:
            try:
                detail = res.json().get("detail", res.text)
            except ValueError:
                detail = res.text
            raise ApiError(f"{res.status_code}: {detail}")
        return res

    def get(self, path: str, **params: Any) -> Any:
        return self._call(
            "GET", path, params={k: v for k, v in params.items() if v not in (None, "", [])}
        ).json()

    def post(self, path: str, *, params: dict | None = None, **kwargs: Any) -> Any:
        return self._call("POST", path, params=params, **kwargs).json()

    def download(self, path: str, **params: Any) -> bytes:
        return self._call(
            "GET", path, params={k: v for k, v in params.items() if v not in (None, "", [])}
        ).content

    def health(self) -> dict | None:
        try:
            return self.get("/health")
        except ApiError:
            return None
