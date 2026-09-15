"""Cliente HTTP da API do AgriShield."""

import httpx
import streamlit as st

from config import API_URL


class ApiClient:
    def __init__(self, base_url: str = API_URL, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url=f"{base_url}/api/v1", timeout=10.0, transport=transport
        )

    def health(self) -> dict:
        response = self._client.get("/health")
        response.raise_for_status()
        return response.json()


@st.cache_resource
def get_api() -> ApiClient:
    return ApiClient()
