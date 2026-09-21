"""Relatórios e tendências de risco (W12).

Três recortes, um por perfil de usuário: **equipamento** (gestor de frota), **região** (analista
da seguradora) e **cultura** (subscrição e produto). Todos os números, inclusive as
porcentagens, vêm prontos da API — o front não agrega nada. O CSV também é gerado pela API
(UTF-8 com BOM, `;` e vírgula decimal): aqui só entra o link de download.
"""

import altair as alt
import pandas as pd
import streamlit as st

from components.device_picker import device_picker
from components.farm_picker import farm_picker
from components.formatting import brl, num, short_date
from services.api_client import (
    ApiError,
    get_crop_report,
    get_equipment_report,
    get_farm,
    get_region_report,
    report_csv_url,
)

# Safras cobertas pela base do PSR/SISSER carregada (D1, ver document/dados-e-modelo.md).
PSR_FIRST_YEAR = 2006
PSR_LAST_YEAR = 2025

DEFAULT_TREND_DAYS = 7
MAX_TREND_DAYS = 90

# UFs do Brasil, para o filtro de região (dado de referência, não regra de negócio).
STATES = (
    "AC",
    "AL",
    "AP",
    "AM",
    "BA",
    "CE",
    "DF",
    "ES",
    "GO",
    "MA",
    "MT",
    "MS",
    "MG",
    "PA",
    "PB",
    "PR",
    "PE",
    "PI",
    "RJ",
    "RN",
    "RS",
    "RO",
    "RR",
    "SC",
    "SP",
    "SE",
    "TO",
)

# Formatação das colunas numéricas das tabelas, para casarem com os KPIs ao lado.
MONEY_COLUMN = st.column_config.NumberColumn("Indenização (R$)", format="R$ %.2f")
PERCENT_COLUMN = st.column_config.NumberColumn("Taxa (%)", format="%.1f%%")
COUNT_COLUMN_KWARGS = {"format": "%d"}

EVENT_COLOR = "#1565C0"
HOURS_COLOR = "#2E7D32"
ABOVE_LIMIT_COLOR = "#C62828"


def _purpose(report: dict) -> None:
    """Para quem serve e que decisão apoia — vem escrito da API."""
    purpose = report.get("purpose")
    if purpose:
        st.info(purpose, icon="🎯")


def _csv_button(label: str, path: str, params: dict) -> None:
    st.link_button(
        f"⬇️ {label}",
        report_csv_url(path, params),
        help="O arquivo é gerado pela API, em UTF-8 com BOM e separador `;` (abre no Excel).",
    )


def _year_filter(key_prefix: str) -> tuple[int | None, int | None]:
    """Filtro de safra, desligado por padrão: sem ele a API devolve a série inteira."""
    if not st.checkbox("Filtrar por safra", key=f"{key_prefix}_filter_years"):
        st.caption(f"Sem filtro: todas as safras carregadas ({PSR_FIRST_YEAR}–{PSR_LAST_YEAR}).")
        return None, None
    first, last = st.slider(
        "Safras",
        min_value=PSR_FIRST_YEAR,
        max_value=PSR_LAST_YEAR,
        value=(PSR_FIRST_YEAR, PSR_LAST_YEAR),
        key=f"{key_prefix}_years",
    )
    if (first, last) == (PSR_FIRST_YEAR, PSR_LAST_YEAR):
        # Faixa cheia é o mesmo que não filtrar: evita uma segunda entrada de cache (e um
        # segundo link de CSV) para a mesma consulta de 1,5 milhão de linhas.
        st.caption("Faixa cheia: a consulta vai sem filtro de safra.")
        return None, None
    return first, last


# ---------------------------------------------------------------- equipamento


def _equipment_charts(trend: list[dict]) -> None:
    frame = pd.DataFrame(
        [
            {
                "Dia": short_date(day["date"]),
                "Horas operando": day["operating_hours"],
                "% acima do limite": day["pct_time_above_limit"],
                "Alertas": day["alerts"],
            }
            for day in trend
        ]
    )
    axis_x = alt.X("Dia:N", sort=None, title=None)

    hours = (
        alt.Chart(frame)
        .mark_bar(color=HOURS_COLOR, size=24, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(x=axis_x, y=alt.Y("Horas operando:Q", title="horas"), tooltip=list(frame.columns))
        .properties(height=200)
    )
    above = (
        alt.Chart(frame)
        .mark_line(color=ABOVE_LIMIT_COLOR, strokeWidth=2, point=True)
        .encode(
            x=axis_x,
            y=alt.Y("% acima do limite:Q", title="% do tempo"),
            tooltip=list(frame.columns),
        )
        .properties(height=200)
    )

    left, right = st.columns(2)
    with left:
        st.markdown("**Horas operando por dia**")
        st.altair_chart(hours, width="stretch")
    with right:
        st.markdown("**% do tempo acima do limite**")
        st.altair_chart(above, width="stretch")

    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "Horas operando": st.column_config.NumberColumn("Horas operando", format="%.1f h"),
            "% acima do limite": st.column_config.NumberColumn(
                "% acima do limite", format="%.1f%%"
            ),
            "Alertas": st.column_config.NumberColumn("Alertas", format="%d"),
        },
    )


def _equipment_tab(farm: dict) -> None:
    device = device_picker(farm)
    if device is None:
        return

    days = st.slider(
        "Período (dias)",
        min_value=1,
        max_value=MAX_TREND_DAYS,
        value=DEFAULT_TREND_DAYS,
        key="report_equipment_days",
    )

    try:
        report = get_equipment_report(device["device_id"], days=days)
    except ApiError as error:
        st.error(str(error))
        return

    _purpose(report)

    first, second, third, fourth = st.columns(4)
    first.metric("Horas operando", f"{num(report['operating_hours'])} h")
    second.metric("Tempo acima do limite", f"{num(report['pct_time_above_limit'])}%")
    third.metric("Alertas", f"{report['alerts']}")
    fourth.metric("Capotamentos", f"{report['rollovers']}")
    st.caption(
        f"{report['readings']} leituras nos últimos {report['days']} dias. As horas operando "
        "são estimadas pela contagem de leituras (a API explica o método)."
    )

    trend = report.get("trend") or []
    if trend:
        _equipment_charts(trend)
    else:
        st.info(
            "Sem telemetria neste período. Ligue o equipamento no Wokwi e o relatório se enche "
            "sozinho.",
            icon="⏳",
        )

    _csv_button(
        "Baixar CSV do equipamento",
        f"/reports/equipment/{device['device_id']}.csv",
        {"days": days},
    )


# ---------------------------------------------------------------- região


def _event_chart(events: list[dict], title: str) -> None:
    frame = pd.DataFrame(
        [
            {
                "Causa": event["event_category"],
                "Sinistros": event["claims"],
                "% dos sinistros": event["pct_of_claims"],
                "Indenização (R$)": event["indemnity_total"],
            }
            for event in events
        ]
    )
    chart = (
        alt.Chart(frame)
        .mark_bar(color=EVENT_COLOR, cornerRadiusEnd=4)
        .encode(
            y=alt.Y("Causa:N", sort="-x", title=None),
            x=alt.X("% dos sinistros:Q", title="% dos sinistros"),
            tooltip=list(frame.columns),
        )
        .properties(height=min(40 * len(frame), 320))
    )
    st.markdown(f"**{title}**")
    st.altair_chart(chart, width="stretch")


def _region_tab(farm: dict) -> None:
    default_state = farm.get("state", "MG")
    index = STATES.index(default_state) if default_state in STATES else 0
    state = st.selectbox("UF", STATES, index=index, key="report_region_state")
    from_year, to_year = _year_filter("region")

    try:
        report = get_region_report(state, from_year=from_year, to_year=to_year)
    except ApiError as error:
        st.error(str(error))
        return

    _purpose(report)

    first, second, third, fourth = st.columns(4)
    first.metric("Apólices", f"{report['policies']:,}".replace(",", "."))
    second.metric("Sinistros", f"{report['claims']:,}".replace(",", "."))
    third.metric("Taxa de sinistro", f"{num(report['claim_rate_pct'])}%")
    fourth.metric(
        "Indenização total",
        brl(report["indemnity_total"]),
        help=f"Média por sinistro: {brl(report['indemnity_mean'])}.",
    )

    events = report.get("top_events") or []
    municipalities = report.get("top_municipalities") or []
    if not events and not municipalities:
        st.warning("Sem dados de sinistro para este recorte.")
    if events:
        _event_chart(events, f"Causas de sinistro em {state}")
    if municipalities:
        st.markdown("**Municípios com mais sinistros**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Município": row["municipality"],
                        "Apólices": row["policies"],
                        "Sinistros": row["claims"],
                        "Taxa (%)": row["claim_rate_pct"],
                        "Indenização (R$)": row["indemnity_total"],
                    }
                    for row in municipalities
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Apólices": st.column_config.NumberColumn("Apólices", **COUNT_COLUMN_KWARGS),
                "Sinistros": st.column_config.NumberColumn("Sinistros", **COUNT_COLUMN_KWARGS),
                "Taxa (%)": PERCENT_COLUMN,
                "Indenização (R$)": MONEY_COLUMN,
            },
        )

    demo_farms = report.get("demo_farms") or []
    if demo_farms:
        st.markdown("**Fazendas de demonstração nesta UF**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Fazenda": row["farm_id"],
                        "Município": row["municipality"],
                        "Score do terreno": row["terrain_score"],
                        "Classe": row["risk_class"],
                    }
                    for row in demo_farms
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Score do terreno": st.column_config.NumberColumn("Score do terreno", format="%.1f")
            },
        )
        st.caption(
            "O histórico real de sinistros da região ao lado do perfil de terreno da fazenda "
            "(W8) — é esse cruzamento que a subscrição usa."
        )

    _csv_button(
        "Baixar CSV da região",
        "/reports/region.csv",
        {"state": state, "from_year": from_year, "to_year": to_year},
    )


# ---------------------------------------------------------------- cultura


def _crop_tab() -> None:
    use_state = st.checkbox("Filtrar por UF", key="report_crop_filter_state")
    state = st.selectbox("UF", STATES, key="report_crop_state") if use_state else None
    from_year, to_year = _year_filter("crop")

    try:
        report = get_crop_report(from_year=from_year, to_year=to_year, state=state)
    except ApiError as error:
        st.error(str(error))
        return

    _purpose(report)

    first, second, third = st.columns(3)
    first.metric("Apólices", f"{report['policies']:,}".replace(",", "."))
    second.metric("Sinistros", f"{report['claims']:,}".replace(",", "."))
    third.metric("Taxa de sinistro", f"{num(report['claim_rate_pct'])}%")

    crops = report.get("crops") or []
    if crops:
        st.markdown("**Culturas, da maior para a menor taxa de sinistro**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Cultura": row["crop"],
                        "Apólices": row["policies"],
                        "Sinistros": row["claims"],
                        "Taxa (%)": row["claim_rate_pct"],
                        "Indenização (R$)": row["indemnity_total"],
                        "Causa mais frequente": row["top_event"] or "—",
                    }
                    for row in crops
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Apólices": st.column_config.NumberColumn("Apólices", **COUNT_COLUMN_KWARGS),
                "Sinistros": st.column_config.NumberColumn("Sinistros", **COUNT_COLUMN_KWARGS),
                "Taxa (%)": PERCENT_COLUMN,
                "Indenização (R$)": MONEY_COLUMN,
            },
        )
    else:
        st.warning("Sem dados de sinistro para este recorte.")

    events = report.get("events") or []
    if events:
        _event_chart(events, "Causas de sinistro no recorte")

    _csv_button(
        "Baixar CSV por cultura",
        "/reports/crop.csv",
        {"from_year": from_year, "to_year": to_year, "state": state},
    )


# ---------------------------------------------------------------- página


st.title("📊 Relatórios")
st.caption(
    "Tendências de risco por equipamento, região e tipo de operação. Os recortes de região e "
    "cultura vêm de 1,5 milhão de apólices reais do PSR/SISSER."
)

selected_farm = farm_picker()
if selected_farm is None:
    st.error(
        "Nenhuma fazenda disponível para exibir. Verifique se a API está no ar "
        "(`uv run fastapi dev app/main.py` na pasta api/) e recarregue a página."
    )
    st.stop()

try:
    farm_detail = get_farm(selected_farm["id"])
except ApiError as api_error:
    st.error(str(api_error))
    st.stop()

equipment_tab, region_tab, crop_tab = st.tabs(["🚜 Equipamento", "🗺️ Região", "🌾 Cultura"])

with equipment_tab:
    _equipment_tab(farm_detail)

with region_tab:
    _region_tab(farm_detail)

with crop_tab:
    _crop_tab()
