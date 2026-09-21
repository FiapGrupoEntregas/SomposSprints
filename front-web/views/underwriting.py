"""Subscrição — perfil de risco do terreno para a cotação (W8).

O score, a classe, os indicadores e os drivers vêm prontos de
`GET /api/v1/farms/{id}/underwriting`. O front só organiza na tela: nenhum peso, nenhuma
fórmula e nenhuma classificação são calculados aqui.
"""

import altair as alt
import pandas as pd
import streamlit as st

from components.farm_picker import farm_picker
from components.formatting import num
from services.api_client import ApiError, get_underwriting, list_farms

# Classes de risco do terreno (regras-de-risco §8). Cores: as mesmas dos níveis do projeto.
CLASS_STYLE = {
    "A": ("#2E7D32", "Risco baixo do terreno"),
    "B": ("#F9A825", "Risco médio do terreno"),
    "C": ("#C62828", "Risco alto do terreno"),
}
UNKNOWN_CLASS = ("#9E9E9E", "Classe não informada")

SLOPE_BUCKETS = (
    ("pct_slope_lt8", "Abaixo de 8°", "#2E7D32"),
    ("pct_slope_8_15", "De 8° a 15°", "#F9A825"),
    ("pct_slope_gt15", "15° ou mais", "#C62828"),
)


def _class_badge(profile: dict) -> None:
    color, meaning = CLASS_STYLE.get(profile["risk_class"], UNKNOWN_CLASS)
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:16px;border-left:8px solid {color};'
        'padding:10px 18px;margin-bottom:8px;">'
        f'<div style="font-size:4rem;font-weight:800;line-height:1;color:{color};">'
        f"{profile['risk_class']}</div>"
        f'<div><div style="font-size:2rem;font-weight:700;line-height:1.1;">'
        f'{num(profile["terrain_score"])} <span style="font-size:1rem;font-weight:400;">'
        "/ 100</span></div>"
        f'<div style="font-size:0.95rem;">{meaning} · quanto maior o score, melhor</div></div>'
        "</div>",
        unsafe_allow_html=True,
    )


def _indicators(profile: dict) -> None:
    first, second, third = st.columns(3)
    first.metric(
        "Área com 15° ou mais",
        f"{num(profile['pct_slope_gt15'], 0)}%",
        help=(
            f"Abaixo de 8°: {num(profile['pct_slope_lt8'], 0)}% · "
            f"de 8° a 15°: {num(profile['pct_slope_8_15'], 0)}%."
        ),
    )
    second.metric("Área em baixada", f"{num(profile['pct_lowland'], 0)}%")
    third.metric("Área em topo exposto", f"{num(profile['pct_exposed'], 0)}%")

    fourth, fifth, sixth = st.columns(3)
    fourth.metric("Inclinação máxima", f"{num(profile['slope_max_deg'])}°")
    fifth.metric("Inclinação média", f"{num(profile['slope_mean_deg'])}°")
    sixth.metric(
        "Limite de referência",
        f"{num(profile['reference_tilt_limit_deg'])}°",
        help="Limite do equipamento em solo seco. O score não depende dele (§8).",
    )


def _slope_chart(profile: dict) -> None:
    frame = pd.DataFrame(
        [
            {"Faixa": label, "Área (%)": profile[field], "cor": color}
            for field, label, color in SLOPE_BUCKETS
        ]
    )
    chart = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=48)
        .encode(
            x=alt.X("Faixa:N", sort=None, title=None),
            y=alt.Y("Área (%):Q", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("cor:N", scale=None, legend=None),
            tooltip=["Faixa", "Área (%)"],
        )
        .properties(height=220)
    )
    st.altair_chart(chart, width="stretch")
    st.caption("Distribuição da inclinação da fazenda, em % da área.")


def _drivers(profile: dict) -> None:
    st.markdown("**O que mais pesa**")
    drivers = profile.get("drivers") or []
    if not drivers:
        st.success("Nenhum fator relevante: o relevo não penaliza esta fazenda.")
        return
    for driver in drivers:
        st.markdown(f"- {driver['label']} — **−{num(driver['points'])} pontos**")


def _portfolio(selected_farm_id: str) -> None:
    st.subheader("🗂️ Carteira")
    try:
        farms = list_farms()
    except ApiError as error:
        st.warning(f"Não foi possível listar a carteira agora. {error}")
        return

    rows, failures = [], []
    for farm in farms:
        try:
            profile = get_underwriting(farm["id"])
        except ApiError:
            failures.append(farm["name"])
            continue
        rows.append(
            {
                "": "➡️" if farm["id"] == selected_farm_id else "",
                "Classe": profile["risk_class"],
                "Score": profile["terrain_score"],
                "Fazenda": profile["farm_name"],
                "Município/UF": f"{profile['municipality']}/{profile['state']}",
                "Cultura": profile["crop"],
                "≥ 15° (%)": profile["pct_slope_gt15"],
                "Baixada (%)": profile["pct_lowland"],
                "Topo exposto (%)": profile["pct_exposed"],
            }
        )

    if rows:
        frame = pd.DataFrame(rows).sort_values("Score", ascending=False)
        st.dataframe(frame, hide_index=True, width="stretch")
        st.caption("Ordenada pelo score do terreno, do melhor para o pior.")
    if failures:
        st.caption(f"Sem perfil agora para: {', '.join(failures)}.")


def _calibration_note(profile: dict) -> None:
    st.info(
        f"**Pesos {profile['weights_version']}.** {profile['calibration_note']}",
        icon="🧪",
    )


st.title("📋 Subscrição")
st.caption(
    "Perfil de risco do terreno para a cotação: sem questionário e sem hardware, só o relevo "
    "real da área."
)

selected_farm = farm_picker()
if selected_farm is None:
    st.error(
        "Nenhuma fazenda disponível para exibir. Verifique se a API está no ar "
        "(`uv run fastapi dev app/main.py` na pasta api/) e recarregue a página."
    )
    st.stop()

try:
    farm_profile = get_underwriting(selected_farm["id"])
except ApiError as api_error:
    st.error(str(api_error))
    st.stop()

st.subheader(f"{farm_profile['farm_name']}")
st.caption(
    f"{farm_profile['municipality']}/{farm_profile['state']} · cultura: {farm_profile['crop']}"
)

_class_badge(farm_profile)
_calibration_note(farm_profile)
_indicators(farm_profile)

chart_column, drivers_column = st.columns([3, 2])
with chart_column:
    _slope_chart(farm_profile)
with drivers_column:
    _drivers(farm_profile)

st.divider()
_portfolio(selected_farm["id"])
