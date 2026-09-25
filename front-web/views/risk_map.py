"""Mapa de risco da fazenda — abas Relevo (W1 + W2) e Previsão de risco (W3).

A página só desenha o que a API manda: polígonos, inclinação, classe de terreno,
estado do solo, limite do dia, nível de cada célula e os motivos em português vêm
prontos de `/api/v1/farms/{farm_id}/terrain` e `/api/v1/farms/{farm_id}/risk`.
Nenhum nível, limiar ou limite é calculado aqui.
"""

import altair as alt
import pandas as pd
import streamlit as st

from components.cell_map import hex_to_rgb, render_cell_map
from components.farm_picker import farm_picker
from components.formatting import SOIL_LABELS, level_style
from components.formatting import num as _num
from components.formatting import short_date as _short_date
from components.state import EXPERIMENTAL_MLP_KEY, SCENARIO_KEY, SELECTED_DATE_KEY
from services.api_client import (
    ApiError,
    get_farm,
    get_recommendations,
    get_risk,
    get_terrain,
)

# ---------------------------------------------------------------- aba Relevo (W2)

# Opções do seletor de cor do mapa de relevo.
COLOR_BY_SLOPE = "Inclinação"
COLOR_BY_CLASS = "Classe de terreno"

# Escala sequencial de inclinação: uma cor só, do claro (plano) ao escuro (íngreme).
# Não é a escala de risco (🟢🟡🔴): aqui o que é medido é o terreno, não o perigo do dia.
SLOPE_RAMP_HEX = ("#FBECD9", "#F2C48A", "#E28B3C", "#BF5B14", "#7A3B0A")
# Fundo de escala para não exagerar o contraste numa fazenda praticamente plana.
MIN_SLOPE_SCALE_DEG = 5.0

# Classes de terreno (document/regras-de-risco.md §1). Paleta categórica validada
# para daltonismo (deutan/tritan ΔE ≥ 13) sobre fundo claro.
TERRAIN_CLASSES = {
    "lowland": ("Baixada", "#1565C0", "Parte baixa: acumula água e demora a secar."),
    "slope": ("Encosta", "#EF6C00", "Meia-encosta: é onde a inclinação pesa."),
    "exposed": ("Topo exposto", "#9C4DCC", "Crista ou topo: vento e descarga elétrica."),
    "flat": ("Plano", "#4E7A3A", "Terreno sem desnível relevante."),
}
UNKNOWN_CLASS = ("Não informado", "#9E9E9E", "Classe não reconhecida.")

FILL_ALPHA = 190

# ---------------------------------------------------------------- aba Risco (W3)

FORECAST_DAYS = 7
HEAVY_RAIN_SCENARIO = "heavy_rain"
SCENARIO_LABELS = {HEAVY_RAIN_SCENARIO: "chuva forte"}

LEVEL_ORDER = ("red", "yellow", "green")

# Perigos avaliados pela API (W3 + W7). Ícones e rótulos são só apresentação.
HAZARD_STYLE = {
    "rollover": ("🚜", "Capotamento"),
    "bogging": ("🟫", "Atolamento"),
    "lightning": ("⚡", "Raio"),
    "wind": ("💨", "Vento"),
    "fire": ("🔥", "Incêndio"),
}
UNKNOWN_HAZARD = ("•", "Outro perigo")
HAZARD_FILTER_KEY = "risk_hazard_filter"

RECOMMENDATION_DAYS = 2

# W13 — o cartão do modelo é uma **segunda leitura**: o alerta continua sendo o nível por regras.
MODEL_BORDER_COLOR = "#78909C"
# Com duas casas, qualquer valor abaixo disso imprimiria "0,00%", que parece defeito na tela.
MIN_SHOWN_PROBABILITY_PCT = 0.005
# Nomes amigáveis das variáveis do modelo (D3). Sem tradução conhecida, mostra o nome cru.
FEATURE_LABELS = {
    "state": "UF da fazenda",
    "crop": "cultura",
    "dry_spell_max_days": "maior período seco",
    "elevation_range_m": "amplitude do terreno",
    "slope_max_deg": "inclinação máxima",
    "slope_mean_deg": "inclinação média",
    "rain_total_mm": "chuva acumulada",
    "temp_max_c": "temperatura máxima",
    "policy_year": "safra",
}
# Frase obrigatória da W6 (critério "janela segura ≠ área liberada"): sai da lista e vira aviso.
# Acoplamento consciente por texto: a frase nasce em `RED_AREAS_STILL_FORBIDDEN_MESSAGE`,
# em api/app/services/recommendations.py (~linha 57). Mudou lá, mude este trecho junto.
DISCLAIMER_HINT = "seguem proibidas"

# Fronteiras do estado do solo (document/regras-de-risco.md §3). Aqui elas são apenas
# as linhas de referência do gráfico de chuva: quem classifica o solo é a API.
SOIL_MOIST_MM = 10.0
SOIL_SATURATED_MM = 30.0

RAIN_DAY_COLOR = "#90CAF9"
RAIN_72H_COLOR = "#1565C0"
REFERENCE_LINE_COLOR = "#757575"
RISK_FILL_ALPHA = 175


# ---------------------------------------------------------------- aba Relevo


def _slope_color(slope_deg: float, slope_max_deg: float) -> list[int]:
    """Cor da célula na escala sequencial, relativa ao máximo da própria fazenda."""
    ratio = min(max(slope_deg / slope_max_deg, 0.0), 1.0) if slope_max_deg > 0 else 0.0
    position = ratio * (len(SLOPE_RAMP_HEX) - 1)
    low = int(position)
    high = min(low + 1, len(SLOPE_RAMP_HEX) - 1)
    weight = position - low
    start, end = hex_to_rgb(SLOPE_RAMP_HEX[low]), hex_to_rgb(SLOPE_RAMP_HEX[high])
    return [round(start[i] + (end[i] - start[i]) * weight) for i in range(3)]


def _swatch(color: str, title: str, description: str) -> str:
    return (
        '<div style="display:flex;align-items:center;gap:8px;margin:2px 0;">'
        f'<span style="width:14px;height:14px;border-radius:3px;background:{color};'
        'display:inline-block;flex:none;"></span>'
        f"<span><b>{title}</b> — {description}</span></div>"
    )


def _slope_legend(slope_max_deg: float, slope_scale_deg: float) -> None:
    """Barra da escala de cor.

    `slope_max_deg` é o valor real vindo da API; `slope_scale_deg` é o fim da escala de cor,
    que numa fazenda quase plana fica no piso de `MIN_SLOPE_SCALE_DEG`.
    """
    gradient = ", ".join(SLOPE_RAMP_HEX)
    scale_end = (
        f"{_num(slope_scale_deg)}°"
        if slope_scale_deg <= slope_max_deg
        else f"{_num(slope_scale_deg)}° (escala mínima)"
    )
    st.markdown(
        f'<div style="max-width:420px;">'
        f'<div style="height:12px;border-radius:3px;'
        f'background:linear-gradient(90deg, {gradient});"></div>'
        f'<div style="display:flex;justify-content:space-between;font-size:0.8rem;">'
        f"<span>0°</span><span>{scale_end}</span></div>"
        "</div>",
        unsafe_allow_html=True,
    )
    caption = (
        f"Quanto mais escuro, mais íngreme. Inclinação máxima desta fazenda: "
        f"{_num(slope_max_deg)}°. A escala é relativa à própria fazenda, então duas fazendas "
        "diferentes não se comparam pela cor — compare pelos números."
    )
    if slope_scale_deg > slope_max_deg:
        caption += (
            f" Como o terreno é quase plano, a barra vai até {_num(slope_scale_deg)}° "
            "para não exagerar diferenças de poucos décimos de grau."
        )
    st.caption(caption)


def _class_legend() -> None:
    swatches = "".join(
        _swatch(color, title, description) for title, color, description in TERRAIN_CLASSES.values()
    )
    st.markdown(f'<div style="font-size:0.9rem;">{swatches}</div>', unsafe_allow_html=True)
    st.caption(
        "São cores de **tipo de terreno**, não de risco. O risco do dia (🟢 🟡 🔴) aparece na aba "
        "Previsão de risco."
    )


def _terrain_rows(cells: list[dict], color_by: str, slope_max_deg: float) -> list[dict]:
    """Traduz as células do relevo em linhas do pydeck (cor + textos do tooltip)."""
    rows = []
    for cell in cells:
        title, class_color, _ = TERRAIN_CLASSES.get(cell["terrain_class"], UNKNOWN_CLASS)
        color = (
            _slope_color(cell["slope_deg"], slope_max_deg)
            if color_by == COLOR_BY_SLOPE
            else hex_to_rgb(class_color)
        )
        rows.append(
            {
                "polygon": cell["polygon"],
                "fill_color": [*color, FILL_ALPHA],
                "elevation_text": f"{_num(cell['elevation_m'], 0)} m",
                "slope_text": f"{_num(cell['slope_deg'])}°",
                "aspect_text": f"{cell['aspect_label']} ({_num(cell['aspect_deg'], 0)}°)",
                "class_text": title,
            }
        )
    return rows


TERRAIN_TOOLTIP = (
    "<b>Elevação:</b> {elevation_text}<br/>"
    "<b>Inclinação:</b> {slope_text}<br/>"
    "<b>Orientação:</b> {aspect_text}<br/>"
    "<b>Terreno:</b> {class_text}"
)


def _render_terrain_tab(farm: dict) -> None:
    try:
        terrain = get_terrain(farm["id"])
    except ApiError as error:
        st.error(str(error))
        return

    stats = terrain["stats"]
    cells = terrain.get("cells", [])
    if not cells:
        st.warning("A API não devolveu células de relevo para esta fazenda.")
        return

    left, middle, right = st.columns(3)
    left.metric(
        "Amplitude do terreno",
        f"{_num(stats['elevation_range_m'], 0)} m",
        help=(
            f"Do ponto mais baixo ({_num(stats['elevation_min_m'], 0)} m) ao mais alto "
            f"({_num(stats['elevation_max_m'], 0)} m)."
        ),
    )
    middle.metric(
        "Inclinação máxima",
        f"{_num(stats['slope_max_deg'])}°",
        help=f"Inclinação média da fazenda: {_num(stats['slope_mean_deg'])}°.",
    )
    right.metric(
        "Área com 15° ou mais",
        f"{_num(stats['pct_slope_gt15'], 0)}%",
        help=(
            f"Abaixo de 8°: {_num(stats['pct_slope_lt8'], 0)}% · "
            f"de 8° a 15°: {_num(stats['pct_slope_8_15'], 0)}%."
        ),
    )

    color_by = st.radio(
        "Colorir o mapa por",
        (COLOR_BY_SLOPE, COLOR_BY_CLASS),
        horizontal=True,
        key="terrain_color_by",
    )

    slope_scale = max(stats["slope_max_deg"], MIN_SLOPE_SCALE_DEG)
    render_cell_map(farm["center"], _terrain_rows(cells, color_by, slope_scale), TERRAIN_TOOLTIP)

    cell_size = terrain["cell_size_m"]
    st.caption(
        f"Grade de {terrain['grid_size']}×{terrain['grid_size']} células de "
        f"~{_num(cell_size['x_m'], 0)} m × {_num(cell_size['y_m'], 0)} m. "
        "Passe o mouse sobre uma célula para ver elevação, inclinação, orientação e terreno."
    )

    if color_by == COLOR_BY_SLOPE:
        _slope_legend(stats["slope_max_deg"], slope_scale)
    else:
        _class_legend()


# ---------------------------------------------------------------- aba Previsão de risco


def _scenario_banner(scenario: str) -> None:
    """Faixa obrigatória quando a previsão exibida foi alterada por um cenário simulado."""
    label = SCENARIO_LABELS.get(scenario, scenario)
    st.warning(
        f"⚠️ **Cenário simulado — não é a previsão real.** A chuva de *{label}* foi somada à "
        "previsão da Open-Meteo só para demonstração. Desligue o cenário para ver o tempo real.",
        icon="⚠️",
    )


def _day_card(day: dict, selected: bool) -> str:
    emoji, level_label, color = level_style(day["worst_level"])
    border = "3px" if selected else "1px"
    return (
        f'<div style="border:{border} solid {color};border-top:5px solid {color};'
        'border-radius:6px;padding:6px 8px;margin-bottom:4px;min-height:104px;">'
        f'<div style="font-weight:600;font-size:0.85rem;">{_short_date(day["date"])}</div>'
        f'<div style="font-size:0.95rem;">{emoji} {level_label}</div>'
        f'<div style="font-size:0.8rem;">🌧️ {_num(day["rain_mm"])} mm</div>'
        f'<div style="font-size:0.8rem;">{SOIL_LABELS.get(day["soil_state"], day["soil_state"])}'
        "</div></div>"
    )


def _select_day(date: str) -> None:
    """Callback do botão do card.

    Roda **antes** do corpo do script, então os cards já são desenhados com o dia novo
    destacado — com a escrita no corpo, o destaque atrasaria uma interação. Guarda a data
    (não o índice) porque ela é a chave compartilhada com o card do limite (W4).
    """
    st.session_state[SELECTED_DATE_KEY] = date


def _day_strip(days: list[dict]) -> int:
    """Faixa dos 7 dias. Devolve o índice do dia escolhido (a data fica no session_state)."""
    dates = [day["date"] for day in days]
    saved = st.session_state.get(SELECTED_DATE_KEY)
    index = dates.index(saved) if saved in dates else 0
    st.session_state[SELECTED_DATE_KEY] = dates[index]
    columns = st.columns(len(days))
    for position, (column, day) in enumerate(zip(columns, days, strict=True)):
        with column:
            st.markdown(_day_card(day, position == index), unsafe_allow_html=True)
            st.button(
                "Ver dia",
                key=f"risk_day_button_{position}",
                width="stretch",
                type="primary" if position == index else "secondary",
                on_click=_select_day,
                args=(day["date"],),
            )
    return index


def _hazard_style(hazard: str) -> tuple[str, str]:
    return HAZARD_STYLE.get(hazard, UNKNOWN_HAZARD)


def _hazard_filter() -> set[str]:
    """Filtro de exibição da W7. Nada marcado é tratado como 'todos'."""
    chosen = st.multiselect(
        "Perigos",
        list(HAZARD_STYLE),
        default=list(HAZARD_STYLE),
        format_func=lambda hazard: " ".join(_hazard_style(hazard)),
        key=HAZARD_FILTER_KEY,
        help="Filtra o que aparece no mapa e nos motivos. Não muda o cálculo da API.",
    )
    return set(chosen) if chosen else set(HAZARD_STYLE)


def _selected_reasons(reasons: list[dict], hazards: set[str]) -> list[dict]:
    return [reason for reason in reasons if reason["hazard"] in hazards]


def _worst_level(reasons: list[dict]) -> str:
    """Pior nível entre os motivos dados; sem motivo, 🟢.

    É o filtro de exibição da W7 ("o mapa recalcula a cor como o pior nível entre os perigos
    selecionados"), não uma regra nova: os níveis de cada perigo vêm prontos da API.
    """
    levels = {reason["level"] for reason in reasons}
    for level in LEVEL_ORDER:
        if level in levels:
            return level
    return "green"


def _risk_rows(day: dict, terrain: dict, hazards: set[str]) -> list[dict]:
    """Junta o nível de cada célula (risco) com o polígono correspondente (relevo)."""
    polygons = {(cell["row"], cell["col"]): cell["polygon"] for cell in terrain.get("cells", [])}
    rows = []
    for cell in day.get("cells", []):
        polygon = polygons.get((cell["row"], cell["col"]))
        if polygon is None:
            continue
        reasons = _selected_reasons(cell.get("reasons", []), hazards)
        emoji, level_label, color = level_style(_worst_level(reasons))
        reasons_text = "<br/>".join(
            f"{_hazard_style(reason['hazard'])[0]} {reason['message']}" for reason in reasons
        )
        rows.append(
            {
                "polygon": polygon,
                "fill_color": [*hex_to_rgb(color), RISK_FILL_ALPHA],
                "level_text": f"{emoji} {level_label}",
                "reasons_text": reasons_text or "Sem alertas para este dia.",
            }
        )
    return rows


RISK_TOOLTIP = "<b>Risco:</b> {level_text}<br/>{reasons_text}"


def _rain_chart(days: list[dict]) -> None:
    """Chuva do dia (barras) e chuva acumulada em 72 h (linha), na mesma escala em mm."""
    frame = pd.DataFrame(
        [
            {
                "Dia": _short_date(day["date"]),
                "Chuva do dia (mm)": day["rain_mm"],
                "Chuva em 72 h (mm)": day["rain_72h_mm"],
            }
            for day in days
        ]
    )
    axis_x = alt.X("Dia:N", sort=None, title=None)
    bars = (
        alt.Chart(frame)
        .mark_bar(size=18, color=RAIN_DAY_COLOR, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=axis_x,
            y=alt.Y("Chuva do dia (mm):Q", title="mm"),
            tooltip=["Dia", "Chuva do dia (mm)", "Chuva em 72 h (mm)"],
        )
    )
    line = (
        alt.Chart(frame)
        .mark_line(color=RAIN_72H_COLOR, strokeWidth=2, point=True)
        .encode(x=axis_x, y=alt.Y("Chuva em 72 h (mm):Q", title="mm"))
    )
    references = pd.DataFrame(
        {
            "mm": [SOIL_MOIST_MM, SOIL_SATURATED_MM],
            "Referência": [
                f"{_num(SOIL_MOIST_MM, 0)} mm — solo úmido",
                f"{_num(SOIL_SATURATED_MM, 0)} mm — solo encharcado",
            ],
        }
    )
    rules = (
        alt.Chart(references)
        .mark_rule(color=REFERENCE_LINE_COLOR, strokeDash=[5, 4])
        .encode(y="mm:Q", tooltip=["Referência"])
    )
    labels = (
        alt.Chart(references)
        .mark_text(align="left", dx=4, dy=-6, fontSize=11, color=REFERENCE_LINE_COLOR)
        .encode(y="mm:Q", text="Referência:N")
    )
    st.altair_chart((bars + line + rules + labels).properties(height=240), width="stretch")
    st.caption(
        "Barras claras: chuva do dia. Linha azul: chuva acumulada em 72 h — é ela que define o "
        "estado do solo. As linhas tracejadas marcam 10 mm (solo úmido) e 30 mm (encharcado)."
    )


def _levels_bar(pct_levels: dict) -> str:
    segments = ""
    for level in LEVEL_ORDER:
        percentage = pct_levels.get(level, 0.0)
        if percentage <= 0:
            continue
        _, _, color = level_style(level)
        segments += f'<span style="width:{percentage}%;background:{color};"></span>'
    return (
        '<div style="display:flex;height:14px;border-radius:3px;overflow:hidden;'
        f'background:#E0E0E0;">{segments}</div>'
    )


def _weather_strip(day: dict) -> None:
    """De onde vieram os perigos do dia: os números que a API usou (W3 + W7)."""
    parts = []
    if day.get("temp_max_c") is not None:
        parts.append(f"🌡️ {_num(day['temp_max_c'])} °C")
    if day.get("rh_min_pct") is not None:
        parts.append(f"💧 UR mín. {_num(day['rh_min_pct'], 0)}%")
    if day.get("wind_max_kmh") is not None:
        parts.append(f"🌬️ vento {_num(day['wind_max_kmh'], 0)} km/h")
    if day.get("gust_max_kmh") is not None:
        parts.append(f"💨 rajada {_num(day['gust_max_kmh'], 0)} km/h")
    if day.get("cape_max") is not None:
        parts.append(f"⚡ CAPE {_num(day['cape_max'], 0)} J/kg")
    parts.append("⛈️ tempestade prevista" if day.get("thunderstorm") else "⛅ sem tempestade")
    st.caption(" · ".join(parts))


def _day_panel(day: dict, hazards: set[str]) -> None:
    emoji, level_label, _ = level_style(day["worst_level"])
    soil = SOIL_LABELS.get(day["soil_state"], day["soil_state"])

    st.markdown(f"#### {emoji} Risco {level_label.lower()} — {_short_date(day['date'])}")
    st.caption(f"{soil} · {_num(day['rain_72h_mm'])} mm em 72 h · {_num(day['rain_mm'])} mm no dia")

    st.metric(
        "Limite de inclinação do dia",
        f"{_num(day['tilt_limit_deg'])}°",
        help=(
            "Limite que a API calcula para este dia, a partir do limite do equipamento mais "
            f"restritivo da fazenda e do estado do solo ({soil})."
        ),
    )

    pct_levels = day.get("pct_levels", {})
    st.markdown("**Área da fazenda por nível**")
    st.markdown(_levels_bar(pct_levels), unsafe_allow_html=True)
    for level in LEVEL_ORDER:
        level_emoji, label, _ = level_style(level)
        st.markdown(f"{level_emoji} {label}: **{_num(pct_levels.get(level, 0.0), 0)}%** da área")

    if len(hazards) < len(HAZARD_STYLE):
        st.caption(
            "As porcentagens e o nível do dia vêm da API e consideram **todos** os perigos; "
            "o filtro muda as cores do mapa e a lista de motivos."
        )

    st.markdown("**Principais motivos**")
    all_reasons = day.get("top_reasons", [])
    reasons = _selected_reasons(all_reasons, hazards)
    if not all_reasons:
        st.success("Nenhum alerta para este dia: a fazenda inteira está 🟢.")
        return
    if not reasons:
        st.info("Nenhum motivo dos perigos selecionados neste dia.")
        return
    for reason in reasons:
        level_emoji, _, _ = level_style(reason["level"])
        hazard_emoji, hazard_label = _hazard_style(reason["hazard"])
        st.markdown(f"{level_emoji} {hazard_emoji} **{hazard_label}** — {reason['message']}")


def _window_chip(window: dict) -> str:
    start = str(window["start"])[:2]
    end = str(window["end"])[:2]
    return (
        '<span style="display:inline-block;background:#E8F5E9;color:#1B5E20;'
        "border:1px solid #2E7D32;border-radius:12px;padding:2px 10px;margin:2px 4px 2px 0;"
        f'font-weight:600;">{start}h–{end}h</span>'
    )


def _recommendation_day(recommendation: dict) -> None:
    windows = recommendation.get("windows") or []
    if windows:
        st.markdown(
            "**Janelas seguras:** " + "".join(_window_chip(window) for window in windows),
            unsafe_allow_html=True,
        )
    else:
        st.markdown("**Janelas seguras:** nenhuma neste dia.")

    messages = recommendation.get("messages") or []
    orientations = [message for message in messages if DISCLAIMER_HINT not in message]
    disclaimers = [message for message in messages if DISCLAIMER_HINT in message]

    for message in orientations:
        st.markdown(f"- {message}")
    for message in disclaimers:
        # Destaque obrigatório da W6: janela segura não libera área vermelha.
        st.warning(f"**{message}**", icon="⛔")


def _recommendations_card(farm_id: str, scenario: str | None) -> None:
    st.subheader("✅ O que fazer")
    try:
        recommendations = get_recommendations(farm_id, days=RECOMMENDATION_DAYS, scenario=scenario)
    except ApiError as error:
        st.warning(f"Não foi possível carregar as recomendações agora. {error}")
        return

    if not recommendations:
        st.info("A API não devolveu recomendações para esta fazenda.")
        return

    names = ("Hoje", "Amanhã")
    labels = [
        f"{names[index]} — {_short_date(recommendation['date'])}"
        if index < len(names)
        else _short_date(recommendation["date"])
        for index, recommendation in enumerate(recommendations)
    ]
    for tab, recommendation in zip(st.tabs(labels), recommendations, strict=True):
        with tab:
            _recommendation_day(recommendation)


def _probability_text(probability: float) -> str:
    """Probabilidade em porcentagem, sem imprimir um "0,00%" que parece tela quebrada."""
    percent = probability * 100
    if 0 < percent < MIN_SHOWN_PROBABILITY_PCT:
        return "menor que 0,01%"
    return f"{_num(percent, 2)}%"


def _feature_label(feature: str) -> str:
    friendly = FEATURE_LABELS.get(feature)
    return f"{friendly} (`{feature}`)" if friendly else f"`{feature}`"


def _model_card(day: dict, model: dict | None) -> None:
    """Cartão do modelo preditivo (W13).

    Só aparece quando há probabilidade para o dia: sem modelo (ou com campo nulo) o bloco
    **some**, em vez de mostrar um cartão vazio. Nada aqui é calculado no front — a
    probabilidade, as métricas e a ressalva vêm prontas da API, que as lê do próprio artefato.
    """
    probability = day.get("model_probability")
    if probability is None:
        return

    version = day.get("model_version") or (model or {}).get("version") or "sem versão"
    st.markdown(
        f'<div style="border:1px solid {MODEL_BORDER_COLOR};border-radius:6px;'
        'padding:8px 12px;margin-top:8px;">'
        '<div style="font-size:0.85rem;text-transform:uppercase;letter-spacing:0.5px;">'
        "🤖 Probabilidade de sinistro (modelo)</div>"
        f'<div style="font-size:1.2rem;font-weight:700;line-height:1.2;">'
        f"{_probability_text(probability)}</div>"
        f'<div style="font-size:0.8rem;">modelo {version} · segunda leitura, para a seguradora'
        "</div></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "O alerta ao operador continua sendo o nível 🟢 🟡 🔴 das regras, acima. Este número é "
        "uma leitura complementar."
    )

    if not model:
        return

    algorithm = model.get("algorithm")
    trained_at = model.get("trained_at")
    context = [part for part in (algorithm, trained_at) if part]
    if context:
        st.caption("Treinado em: " + " · ".join(context))

    if model.get("test_pr_auc") is not None and model.get("baseline_pr_auc") is not None:
        st.caption(
            f"AUC-PR no teste: **{_num(model['test_pr_auc'], 3)}** contra "
            f"**{_num(model['baseline_pr_auc'], 3)}** do baseline por regras"
            + (
                f" ({model['test_positives']} sinistros em {model['test_samples']} linhas)."
                if model.get("test_samples")
                else "."
            )
        )

    if model.get("note"):
        # Texto montado pela API a partir do artefato: mostrar como veio, sem reescrever.
        st.markdown(model["note"])

    drivers = model.get("drivers") or []
    if drivers:
        st.markdown("**O que mais pesa no modelo, em geral**")
        st.caption(
            "Importância medida na validação, para o modelo inteiro — não é a explicação deste dia."
        )
        for driver in drivers:
            st.markdown(
                f"- {_feature_label(driver['feature'])} — "
                f"importância {_num(driver['importance'], 4)}"
            )


def _experimental_mlp_card(day: dict, info: dict | None) -> None:
    """Exibe a leitura experimental apenas após opt-in, sem sugerir calibração."""
    if info is None:
        return
    if not info.get("available"):
        st.info(info["note"])
        return

    score = day.get("experimental_mlp_score")
    st.markdown("**🧪 Score experimental da MLP**")
    if score is None:
        st.info("A MLP não produziu score para este dia.")
    else:
        version = info.get("version") or "sem versão"
        st.metric("Score experimental MLP (escala 0–1)", _num(score, 4))
        st.caption(f"Experimento {version}; não é uma probabilidade calibrada.")
    st.caption(info["note"])


def _render_risk_tab(farm: dict) -> None:
    # Sem `key=`: estado de widget some na troca de página. A escolha vive na chave própria.
    scenario_on = st.toggle(
        "Cenário: chuva forte (simulado)",
        value=bool(st.session_state.get(SCENARIO_KEY, False)),
        help=(
            "Soma uma frente de chuva forte à previsão real para demonstrar como o risco muda. "
            "Com o cenário ligado, os números NÃO são a previsão real."
        ),
    )
    st.session_state[SCENARIO_KEY] = scenario_on
    scenario = HEAVY_RAIN_SCENARIO if scenario_on else None

    experimental_mlp_on = st.toggle(
        "Mostrar score experimental da MLP",
        value=bool(st.session_state.get(EXPERIMENTAL_MLP_KEY, False)),
        help=(
            "Solicita uma leitura experimental separada. Ela não é calibrada e não altera "
            "níveis de risco, limites, recomendações ou alertas."
        ),
    )
    st.session_state[EXPERIMENTAL_MLP_KEY] = experimental_mlp_on

    try:
        forecast = get_risk(
            farm["id"],
            days=FORECAST_DAYS,
            scenario=scenario,
            include_experimental_mlp=experimental_mlp_on,
        )
        terrain = get_terrain(farm["id"])
    except ApiError as error:
        st.error(str(error))
        return

    if forecast.get("scenario"):
        _scenario_banner(forecast["scenario"])

    days = forecast.get("days") or []
    if not days:
        st.warning("A API não devolveu nenhum dia de previsão para esta fazenda.")
        return

    day = days[_day_strip(days)]
    hazards = _hazard_filter()

    map_column, panel_column = st.columns([3, 2])
    with map_column:
        rows = _risk_rows(day, terrain, hazards)
        if rows:
            render_cell_map(farm["center"], rows, RISK_TOOLTIP)
            legend = (
                "Cada célula está pintada com o nível do dia escolhido: 🟢 baixo, 🟡 atenção, "
                "🔴 alto. Passe o mouse para ver os motivos."
            )
            if len(hazards) < len(HAZARD_STYLE):
                # A imagem precisa se explicar sozinha numa captura de tela.
                shown = ", ".join(
                    " ".join(_hazard_style(hazard)) for hazard in HAZARD_STYLE if hazard in hazards
                )
                legend += f" **Mostrando só: {shown}.**"
            st.caption(legend)
        else:
            st.warning("Não foi possível casar as células do risco com a grade do relevo.")
    with panel_column:
        _weather_strip(day)
        _day_panel(day, hazards)
        _model_card(day, forecast.get("model"))
        _experimental_mlp_card(day, forecast.get("experimental_mlp"))

    # Fora das colunas: em tela estreita elas empilham, e o gráfico fica no fim da página.
    _recommendations_card(farm["id"], scenario)
    _rain_chart(days)


# ---------------------------------------------------------------- página


st.title("🗺️ Mapa de risco")

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

st.subheader(farm_detail["name"])
st.caption(
    f"{farm_detail['municipality']}/{farm_detail['state']} · cultura: {farm_detail['crop']} · "
    f"limite de inclinação de referência em solo seco: "
    f"{_num(farm_detail['reference_tilt_limit_deg'])}°"
)

terrain_tab, risk_tab = st.tabs(["⛰️ Relevo", "🌦️ Previsão de risco"])

with terrain_tab:
    _render_terrain_tab(farm_detail)

with risk_tab:
    _render_risk_tab(farm_detail)
