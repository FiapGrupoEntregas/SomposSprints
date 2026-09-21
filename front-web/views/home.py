import streamlit as st

from components.farm_picker import farm_picker
from services.api_client import ApiError, get_api

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
    version = health.get("version", "desconhecida")
    environment = health.get("environment", "desconhecido")
    st.success(f"API online — versão {version} ({environment})")
except ApiError as error:
    st.error(str(error))

farm = farm_picker()
if farm is not None:
    st.info(
        f"Fazenda selecionada: **{farm['name']}** ({farm['municipality']}/{farm['state']}). "
        "A escolha vale para todas as páginas."
    )
