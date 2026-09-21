"""Replay de acidentes reais — "o sistema teria alertado?" (W9).

A resposta vem inteira da API: veredito, motivos, clima do dia e as limitações. O front não
calcula nada e, principalmente, **não esconde as ressalvas**: um "teríamos alertado" sem a
precisão da coordenada e sem a fonte é uma afirmação que não se sustenta.

Os "não" ficam na mesma hierarquia dos "sim": incêndio por falha mecânica e capotamento em
terreno plano não são o que este motor promete detectar, e dizer isso é parte do argumento.
"""

import datetime as dt

import streamlit as st

from components.cell_map import hex_to_rgb, render_cell_map
from components.formatting import level_style, num, short_date
from services.api_client import (
    ApiError,
    get_replay_summary,
    list_replay_cases,
    run_replay,
)

# Precisão da coordenada do caso (W9). Sempre visível: é a ressalva mais importante.
PRECISION_LABELS = {
    "exato": ("📍", "coordenada exata do acidente"),
    "aproximado": ("📌", "coordenada aproximada"),
    "municipio": (
        "🗺️",
        "ponto típico do município (a notícia não deu a coordenada) — pode estar a "
        "quilômetros da lavoura",
    ),
}
UNKNOWN_PRECISION = ("❔", "precisão da coordenada não informada")

HAZARD_ICONS = {
    "rollover": "🚜",
    "bogging": "🟫",
    "lightning": "⚡",
    "wind": "💨",
    "fire": "🔥",
}

SOURCE_LABELS = {
    "historical_forecast": "Historical Forecast API",
    "archive": "Archive API (sem CAPE)",
}

CELL_ALPHA = 185
MAP_ZOOM = 14.0


def _precision_note(precision: str) -> str:
    icon, text = PRECISION_LABELS.get(precision, UNKNOWN_PRECISION)
    return f"{icon} {text}"


def _case_summary(case: dict) -> None:
    st.markdown(f"#### {case['title']}")
    st.caption(
        f"{short_date(case['date'])} · {case['municipality']}/{case['state']}"
        + (f" · {case['machine']}" if case.get("machine") else "")
    )
    st.markdown(case["description"])
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"**Localização:** {_precision_note(case['location_precision'])}")
    with right:
        st.link_button("🔗 Ver a notícia", case["source_url"])


def _verdict(result: dict) -> None:
    """As duas leituras, com o significado de cada uma — nunca uma só."""
    at_point = result["would_alert"]
    in_grid = result["would_alert_in_grid"]
    emoji, label, color = level_style(result["point_cell"]["level"])
    headline = "✅ Teria alertado no ponto" if at_point else "❌ Não teria alertado no ponto"
    border = "#2E7D32" if at_point else "#C62828"

    st.markdown(
        f'<div style="border-left:8px solid {border};padding:8px 16px;margin:8px 0;">'
        f'<div style="font-size:1.8rem;font-weight:700;line-height:1.2;">{headline}</div>'
        f'<div style="font-size:1rem;">Célula do ponto do acidente: {emoji} {label}</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(result["verdict"])

    grid_text = (
        "✅ **Na vizinhança (grade em volta do ponto):** alguma célula estaria em 🟡 ou 🔴."
        if in_grid
        else "❌ **Na vizinhança (grade em volta do ponto):** nenhuma célula acusaria risco."
    )
    st.markdown(grid_text)
    st.caption(
        "São duas leituras diferentes: **no ponto** é a célula exata da coordenada do caso; "
        "**na grade** é o quadrado de vizinhança em volta dela. Um sim na grade e não no ponto "
        "quer dizer que o perigo estava ao lado — não que o caso foi acertado."
    )


def _limitations(result: dict) -> None:
    """`limitations` nunca vem vazio: vai inteiro na tela, fora de expander e fora de tooltip."""
    limitations = result.get("limitations") or []
    if not limitations:
        return
    body = "\n".join(f"- {item}" for item in limitations)
    st.warning(f"**O que este replay não prova**\n\n{body}", icon="⚠️")


def _point_reasons(result: dict) -> None:
    reasons = result["point_cell"].get("reasons") or []
    st.markdown("**O que o motor viu na célula do acidente**")
    if not reasons:
        st.markdown(
            "- Nenhum perigo acima do limiar naquele dia e naquele ponto. Este caso está fora "
            "do que o motor promete detectar, e isso é informação, não erro."
        )
        return
    for reason in reasons:
        icon = HAZARD_ICONS.get(reason["hazard"], "•")
        emoji, _, _ = level_style(reason["level"])
        st.markdown(f"- {emoji} {icon} {reason['message']}")


def _weather(result: dict) -> None:
    weather = result["weather"]
    st.markdown("**Clima do dia, como o motor o viu**")
    first, second, third, fourth = st.columns(4)
    first.metric("Chuva no dia", f"{num(weather['rain_mm'])} mm")
    second.metric("Chuva em 72 h", f"{num(weather['rain_72h_mm'])} mm")
    third.metric(
        "Rajada máxima",
        f"{num(weather['gust_max_kmh'], 0)} km/h" if weather.get("gust_max_kmh") else "—",
    )
    fourth.metric(
        "Temperatura máx.",
        f"{num(weather['temp_max_c'])} °C" if weather.get("temp_max_c") else "—",
    )
    extras = []
    if weather.get("rh_min_pct") is not None:
        extras.append(f"UR mínima {num(weather['rh_min_pct'], 0)}%")
    if weather.get("cape_max") is not None:
        extras.append(f"CAPE {num(weather['cape_max'], 0)} J/kg")
    extras.append("tempestade registrada" if weather.get("thunderstorm") else "sem tempestade")
    extras.append(f"fonte: {SOURCE_LABELS.get(result['source'], result['source'])}")
    st.caption(" · ".join(extras))


def _grid_map(result: dict) -> None:
    """A grade em volta do ponto, no mapa, com o marcador do acidente por cima.

    Os polígonos vêm de `terrain` (a mesma forma do `/farms/{id}/terrain`) e as cores, do nível
    de cada célula em `day.cells`: o join é por `(row, col)`, como no mapa da fazenda.
    """
    terrain = result.get("terrain") or {}
    polygons = {(cell["row"], cell["col"]): cell for cell in terrain.get("cells", [])}
    location = result["location"]
    marker = {
        "lat": location["lat"],
        "lon": location["lon"],
        "radius": 30,
        "fields": {"rotulo": "📍 Ponto do acidente"},
    }

    if not polygons:
        st.warning(
            "A API não devolveu a grade de relevo deste replay: o mapa mostra só o ponto do "
            "acidente."
        )
        render_cell_map(location, [], RISK_TOOLTIP, zoom=MAP_ZOOM, marker=marker)
        return

    rows = []
    for cell in result["day"].get("cells", []):
        terrain_cell = polygons.get((cell["row"], cell["col"]))
        if terrain_cell is None:
            continue
        emoji, label, color = level_style(cell["level"])
        reasons = "<br/>".join(
            f"{HAZARD_ICONS.get(reason['hazard'], '•')} {reason['message']}"
            for reason in cell.get("reasons") or []
        )
        is_point = (cell["row"], cell["col"]) == (
            result["point_cell"]["row"],
            result["point_cell"]["col"],
        )
        rows.append(
            {
                "polygon": terrain_cell["polygon"],
                "fill_color": [*hex_to_rgb(color), CELL_ALPHA],
                "nivel": f"{emoji} {label}" + (" · célula do acidente" if is_point else ""),
                "inclinacao": f"{num(terrain_cell['slope_deg'])}°",
                "elevacao": f"{num(terrain_cell['elevation_m'], 0)} m",
                "motivos": reasons or "Sem alertas nesta célula.",
            }
        )

    render_cell_map(location, rows, RISK_TOOLTIP, zoom=MAP_ZOOM, marker=marker)

    stats = terrain.get("stats") or {}
    caption = (
        f"Grade de {terrain.get('grid_size', '?')} × {terrain.get('grid_size', '?')} células em "
        "volta do ponto, colorida pelo nível de cada célula. 📍 marca a coordenada do caso."
    )
    if stats.get("slope_max_deg") is not None:
        caption += (
            f" Inclinação máxima da vizinhança: **{num(stats['slope_max_deg'])}°** — passe o "
            "mouse para comparar com a célula do acidente."
        )
    st.caption(caption)


RISK_TOOLTIP = "<b>{nivel}</b><br/>Inclinação: {inclinacao} · elevação: {elevacao}<br/>{motivos}"


def _render_result(result: dict) -> None:
    _verdict(result)
    _limitations(result)
    _grid_map(result)

    left, right = st.columns([3, 2])
    with left:
        _point_reasons(result)
    with right:
        st.markdown("**Coordenada usada**")
        location = result["location"]
        st.caption(
            f"{num(location['lat'], 5)}, {num(location['lon'], 5)} — "
            f"{_precision_note(result['location_precision'])}."
        )

    _weather(result)


def _summary_block() -> None:
    """Placar dos casos, sempre colado à ressalva que a API manda junto."""
    try:
        summary = get_replay_summary()
    except ApiError as error:
        st.warning(f"Não foi possível carregar o placar dos casos agora. {error}")
        return

    evaluated = summary.get("evaluated", 0)
    first, second, third = st.columns(3)
    first.metric("Casos avaliados", f"{evaluated} de {summary.get('total_cases', 0)}")
    second.metric(
        "Alertariam no ponto",
        f"{summary.get('would_alert_at_point', 0)} de {evaluated}",
        help="A célula exata da coordenada do caso estaria em 🟡 ou 🔴.",
    )
    third.metric(
        "Alertariam na grade (vizinhança)",
        f"{summary.get('would_alert_in_grid', 0)} de {evaluated}",
        help="Alguma célula da vizinhança estaria em 🟡 ou 🔴 — não é o mesmo que acertar o caso.",
    )

    # A ressalva vem pronta da API e fica **ao lado do número**: um print do placar sem ela
    # circula sozinho por aí.
    st.warning(summary["note"], icon="🧭")

    failed = summary.get("failed_case_ids") or []
    if failed:
        st.error(
            "Casos que não puderam ser avaliados agora: " + ", ".join(failed) + ".",
            icon="⚠️",
        )

    cases = summary.get("cases") or []
    if not cases:
        return
    st.markdown("**Caso a caso**")
    for case in cases:
        point = "✅" if case["would_alert"] else "❌"
        grid = "✅" if case["would_alert_in_grid"] else "❌"
        st.markdown(
            f"- {point} no ponto · {grid} na grade — **{case['title']}** "
            f"({case['municipality']}/{case['state']}, {short_date(case['date'])}) "
            f"· [notícia]({case['source_url']})"
        )


def _cases_tab() -> None:
    try:
        cases = list_replay_cases()
    except ApiError as error:
        st.error(str(error))
        return

    if not cases:
        st.info("Nenhum caso cadastrado na API.")
        return

    _summary_block()
    st.divider()

    cases_by_id = {case["id"]: case for case in cases}
    chosen = st.selectbox(
        "Caso",
        list(cases_by_id),
        format_func=lambda case_id: (
            f"{cases_by_id[case_id]['title']} — {short_date(cases_by_id[case_id]['date'])} "
            f"({cases_by_id[case_id]['municipality']}/{cases_by_id[case_id]['state']})"
        ),
        key="replay_case",
    )
    _case_summary(cases_by_id[chosen])

    try:
        result = run_replay(case_id=chosen)
    except ApiError as error:
        st.error(str(error))
        return

    st.divider()
    _render_result(result)


def _manual_tab() -> None:
    st.caption(
        "Rode o motor em qualquer ponto e data do passado. A coordenada informada aqui entra "
        "como **aproximada**: só um caso curado pode afirmar uma coordenada exata."
    )
    first, second, third = st.columns(3)
    latitude = first.number_input("Latitude", value=-27.14687, format="%.5f")
    longitude = second.number_input("Longitude", value=-51.74389, format="%.5f")
    day = third.date_input("Data", value=dt.date.today() - dt.timedelta(days=1))

    if not st.button("▶️ Rodar replay", type="primary"):
        return

    try:
        result = run_replay(lat=float(latitude), lon=float(longitude), date=day.isoformat())
    except ApiError as error:
        st.error(str(error))
        return

    st.divider()
    _render_result(result)


st.title("⏪ Replay de acidentes")
st.caption(
    "O mesmo motor do mapa de risco, rodado no dia e no lugar de acidentes que realmente "
    "aconteceram. A pergunta é honesta: o sistema teria alertado?"
)
st.info(
    "O placar não é o produto. Alguns casos **não** seriam alertados — incêndio por falha "
    "mecânica e capotamento em terreno plano estão fora do que este motor detecta, e mostrar "
    "isso vale mais do que escolher casos que acertam.",
    icon="🧭",
)

cases_tab, manual_tab = st.tabs(["📰 Casos reais", "📍 Ponto e data manuais"])

with cases_tab:
    _cases_tab()

with manual_tab:
    _manual_tab()
