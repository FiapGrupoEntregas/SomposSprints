"""Entrada do front-end Streamlit do AgriShield.

Executar: `uv run streamlit run app.py` (ou `streamlit run app.py` com pip).
Cada página fica em views/. Nenhuma regra de negócio aqui: os dados vêm da API.
"""

import streamlit as st

st.set_page_config(page_title="AgriShield", page_icon="🚜", layout="wide")

pages = [
    st.Page("views/home.py", title="Início", icon="🏠", default=True),
    st.Page("views/risk_map.py", title="Mapa de risco", icon="🗺️"),
    st.Page("views/equipment.py", title="Equipamento ao vivo", icon="📡"),
    st.Page("views/underwriting.py", title="Subscrição", icon="📋"),
    st.Page("views/replay.py", title="Replay de acidentes", icon="⏪"),
]

st.navigation(pages).run()
