"""Seletor de equipamento da fazenda, usado no painel ao vivo (W5) e nos relatórios (W12)."""

import streamlit as st

SESSION_KEY = "device_id"


def device_picker(farm: dict, label: str = "Equipamento") -> dict | None:
    """Escolhe um equipamento da fazenda; com um só, devolve ele direto.

    A escolha vai para `st.session_state["device_id"]` — chave própria, para atravessar as
    páginas (ver components/state.py).
    """
    devices = farm.get("devices") or []
    if not devices:
        st.warning("Esta fazenda não tem equipamento cadastrado.")
        return None

    devices_by_id = {device["device_id"]: device for device in devices}
    if len(devices) == 1:
        only = devices[0]
        st.session_state[SESSION_KEY] = only["device_id"]
        return only

    saved = st.session_state.get(SESSION_KEY)
    options = list(devices_by_id)
    index = options.index(saved) if saved in devices_by_id else 0
    chosen = st.selectbox(
        label,
        options,
        index=index,
        format_func=lambda device_id: f"{devices_by_id[device_id]['name']} ({device_id})",
    )
    st.session_state[SESSION_KEY] = chosen
    return devices_by_id[chosen]
