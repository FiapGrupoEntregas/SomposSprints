import httpx
import streamlit as st

from config import API_URL
from services.api_client import get_api

st.title("🚜 Sompo AgriShield")
st.subheader("Prevenção de acidentes com máquinas agrícolas cruzando relevo e clima")

st.markdown(
    """
    **Um terreno inclinado que é seguro hoje pode capotar um trator amanhã.**

    O AgriShield cruza o **relevo** de cada talhão com a **previsão do tempo** para mostrar,
    dia a dia, onde e quando operar máquinas é perigoso — e envia o **limite de inclinação do
    dia** para um dispositivo no próprio equipamento.

    Use o menu lateral para navegar:
    - **Mapa de risco** — relevo e risco dos próximos 7 dias por talhão
    - **Equipamento ao vivo** — inclinação e alertas do ESP32 em tempo real
    - **Subscrição** — perfil de risco do terreno para a seguradora
    - **Replay de acidentes** — o sistema teria avisado?
    """
)

st.divider()

try:
    health = get_api().health()
    st.success(f"API online — versão {health['version']} ({health['environment']})")
except httpx.HTTPError:
    st.error(
        f"API indisponível em {API_URL}. Suba a API com `uv run fastapi dev app/main.py` "
        "na pasta api/."
    )
