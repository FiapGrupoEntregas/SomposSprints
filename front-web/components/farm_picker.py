"""Seletor de fazenda na barra lateral (W1).

A escolha fica em `st.session_state["farm_id"]`, e não na chave do widget, para
sobreviver à troca de página (critério de aceite da W1).
"""

import streamlit as st

from services.api_client import ApiError, list_farms

SESSION_KEY = "farm_id"


def farm_label(farm: dict) -> str:
    """Rótulo da fazenda no seletor: nome, município e cultura."""
    return f"{farm['name']} — {farm['municipality']}/{farm['state']} · {farm['crop']}"


def farm_picker() -> dict | None:
    """Desenha o seletor na barra lateral e devolve a fazenda escolhida (resumo).

    Devolve `None` quando a API está fora do ar ou não tem fazendas; nesse caso o
    próprio seletor já mostra o aviso na barra lateral.
    """
    st.sidebar.subheader("🌱 Fazenda")

    try:
        farms = list_farms()
    except ApiError as error:
        st.sidebar.error(str(error))
        return None

    if not farms:
        st.sidebar.warning("A API não devolveu nenhuma fazenda de demonstração.")
        return None

    farms_by_id = {farm["id"]: farm for farm in farms}
    saved_id = st.session_state.get(SESSION_KEY)
    options = list(farms_by_id)
    index = options.index(saved_id) if saved_id in farms_by_id else 0

    selected_id = st.sidebar.selectbox(
        "Escolha a fazenda",
        options,
        index=index,
        format_func=lambda farm_id: farm_label(farms_by_id[farm_id]),
    )
    st.session_state[SESSION_KEY] = selected_id
    return farms_by_id[selected_id]
