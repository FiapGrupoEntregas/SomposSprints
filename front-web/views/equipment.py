"""Equipamento ao vivo — limite do dia (W4) e painel em tempo real (W5).

Tudo o que aparece aqui vem pronto da API: o limite do dia e o motivo de
`/devices/{id}/limit`, e o estado, a telemetria e os eventos das rotas do painel.
O front não calcula limite, nível nem estado de conexão.
"""

import altair as alt
import pandas as pd
import streamlit as st

from components.device_picker import device_picker
from components.farm_picker import farm_picker
from components.formatting import (
    level_style,
    local_time,
    num,
    seconds_since,
    short_date,
    soil_label,
)
from components.state import SCENARIO_KEY, SELECTED_DATE_KEY
from services.api_client import (
    ApiError,
    ApiKeyError,
    NotFoundError,
    get_device_events,
    get_device_history,
    get_device_limit,
    get_device_status,
    get_farm,
    get_latest_telemetry,
    get_risk,
    get_telemetry_series,
    publish_device_limit,
)

# Chave de sessão própria desta página; o dia e o cenário vêm de components/state.py e são
# os mesmos da aba de risco (W3): escolheu o dia chuvoso lá, é esse dia que se envia aqui.
LAST_PUBLISH_KEY = "last_limit_publish"

HEAVY_RAIN_SCENARIO = "heavy_rain"
SCENARIO_LABELS = {HEAVY_RAIN_SCENARIO: "chuva forte"}

FORECAST_DAYS = 7
LIVE_REFRESH = "2s"
WINDOW_MINUTES = 10
EVENT_LIMIT = 20

# Passaporte (prévia, W11): janela do histórico, nos limites que a API aceita.
DEFAULT_HISTORY_DAYS = 7
MAX_HISTORY_DAYS = 90
# Por quanto tempo um capotamento fica em destaque no topo do painel (W5).
ROLLOVER_HIGHLIGHT_SECONDS = 5 * 60

# Níveis do alerta local do equipamento (contrato-mqtt, `telemetry`).
ALERT_BANNER = {
    "green": ("SEGURO", "#2E7D32"),
    "yellow": ("ATENÇÃO", "#F9A825"),
    "red": ("PERIGO", "#C62828"),
    "rollover": ("🚨 CAPOTAMENTO", "#B71C1C"),
}
UNKNOWN_ALERT = ("SEM LEITURA", "#9E9E9E")

EVENT_STYLE = {
    "tilt_alert": ("⚠️", "Inclinação acima do limite"),
    "rollover": ("🚨", "Capotamento detectado"),
    "incident_report": ("🆘", "Ocorrência registrada pelo operador"),
    "limit_applied": ("⚙️", "Novo limite aplicado no equipamento"),
}
UNKNOWN_EVENT = ("•", "Evento do equipamento")

ROLL_COLOR = "#1565C0"
PITCH_COLOR = "#EF6C00"
LIMIT_LINE_COLOR = "#C62828"


# ---------------------------------------------------------------- W4: limite de hoje


def _forecast_dates(farm_id: str, scenario: str | None) -> list[str]:
    """Dias que a API tem previsão para, na ordem. Lista vazia se a previsão falhar."""
    try:
        forecast = get_risk(farm_id, days=FORECAST_DAYS, scenario=scenario)
    except ApiError:
        return []
    return [day["date"] for day in forecast.get("days", [])]


def _limit_card(limit: dict, device: dict) -> None:
    emoji, level_label, color = level_style(limit["risk_level"])
    reference = limit["reference_tilt_limit_deg"]
    st.markdown(
        f'<div style="border-left:6px solid {color};padding:8px 16px;margin-bottom:8px;">'
        f'<div style="font-size:0.9rem;">Limite de inclinação para '
        f"<b>{device['name']}</b> em {short_date(limit['date'])}</div>"
        f'<div style="font-size:3.2rem;font-weight:700;line-height:1.1;">'
        f"{num(limit['tilt_limit_deg'])}°</div>"
        f'<div style="font-size:0.95rem;">de <b>{num(reference)}°</b> em solo seco · '
        f"risco da fazenda: {emoji} {level_label}</div></div>",
        unsafe_allow_html=True,
    )
    st.caption(f"Motivo enviado ao equipamento: **{limit['reason']}**")

    left, middle, right = st.columns(3)
    left.metric("Estado do solo", soil_label(limit["soil_state"]).replace("solo ", "").capitalize())
    middle.metric("Chuva em 72 h", f"{num(limit['rain_72h_mm'])} mm")
    wind = limit.get("wind_max_kmh")
    right.metric("Vento máximo", f"{num(wind)} km/h" if wind is not None else "—")


def _publish_block(device_id: str, date: str | None, scenario: str | None) -> None:
    if st.button("📡 Enviar ao equipamento", type="primary"):
        try:
            published = publish_device_limit(device_id, date=date, scenario=scenario)
        except ApiKeyError as error:
            # 401 da I5: o problema é de configuração, não do broker.
            st.error(f"{error} O limite **não foi enviado**.")
        except NotFoundError as error:
            st.error(str(error))
        except ApiError as error:
            st.error(
                f"{error} O limite **não foi enviado** agora — nada se perdeu. "
                "Confira se a API está conectada ao broker MQTT e tente de novo."
            )
        else:
            sent_limit = published["limit"]["tilt_limit_deg"]
            st.session_state[LAST_PUBLISH_KEY] = {
                "device_id": device_id,
                "at": published["published_at"],
                "tilt_limit_deg": sent_limit,
                "topic": published["topic"],
            }
            st.toast(f"Limite enviado ao {device_id}: {num(sent_limit)}°", icon="📡")

    last = st.session_state.get(LAST_PUBLISH_KEY)
    if last and last.get("device_id") == device_id:
        st.success(
            f"Último envio: {local_time(last['at'])} — {num(last['tilt_limit_deg'])}°",
            icon="📡",
        )
        st.caption(
            f"Publicado em `{last['topic']}` com QoS 1 e retained: se o equipamento estiver "
            "desligado, ele recebe esse limite assim que reconectar."
        )


def _scenario_banner(scenario: str) -> None:
    label = SCENARIO_LABELS.get(scenario, scenario)
    st.warning(
        f"⚠️ **Cenário simulado — não é a previsão real.** O limite foi calculado com a chuva de "
        f"*{label}* somada à previsão, só para demonstração.",
        icon="⚠️",
    )


def _render_limit_section(farm: dict, device: dict) -> None:
    st.subheader("🎯 Limite de hoje")

    day_column, scenario_column = st.columns([2, 3])
    with scenario_column:
        # Sem `key=`: estado de widget some na troca de página. A escolha vive na chave própria.
        scenario_on = st.toggle(
            "Cenário: chuva forte (simulado)",
            value=bool(st.session_state.get(SCENARIO_KEY, False)),
            help=(
                "O mesmo cenário da aba Previsão de risco. Com ele ligado, o limite mostrado e "
                "enviado NÃO vem da previsão real."
            ),
        )
    st.session_state[SCENARIO_KEY] = scenario_on
    scenario = HEAVY_RAIN_SCENARIO if scenario_on else None

    dates = _forecast_dates(farm["id"], scenario)
    selected_date: str | None = None
    with day_column:
        if dates:
            saved = st.session_state.get(SELECTED_DATE_KEY)
            index = dates.index(saved) if saved in dates else 0
            selected_date = st.selectbox("Dia", dates, index=index, format_func=short_date)
            st.session_state[SELECTED_DATE_KEY] = selected_date
        else:
            st.caption("Não consegui listar os dias da previsão; mostrando o limite de hoje.")

    try:
        limit = get_device_limit(device["device_id"], date=selected_date, scenario=scenario)
    except ApiError as error:
        st.error(str(error))
        return

    if limit.get("scenario"):
        _scenario_banner(limit["scenario"])

    card, side = st.columns([3, 2])
    with card:
        _limit_card(limit, device)
    with side:
        _publish_block(device["device_id"], selected_date, scenario)


# ---------------------------------------------------------------- W5: painel ao vivo


def _status_band(status: dict) -> None:
    online = status.get("state") == "online"
    silence = status.get("seconds_since_last_telemetry")
    if online:
        text = "🟢 **Online**"
        if silence is not None:
            text += f" · última leitura há {num(silence, 0)} s"
    else:
        text = "⚫ **Offline**"
        if silence is not None:
            text += f" há {num(silence, 0)} s sem telemetria"
        elif status.get("last_seen_at") is None:
            text += " · nunca se conectou"
    st.markdown(text)


def _alert_banner(telemetry: dict) -> None:
    label, color = ALERT_BANNER.get(telemetry.get("alert_level"), UNKNOWN_ALERT)
    st.markdown(
        f'<div style="background:{color};color:white;border-radius:8px;padding:14px;'
        f'text-align:center;font-size:1.6rem;font-weight:700;letter-spacing:1px;">{label}</div>',
        unsafe_allow_html=True,
    )


def _telemetry_metrics(telemetry: dict) -> None:
    roll, pitch, limit_metric, climate = st.columns(4)
    roll.metric("Rolagem (roll)", f"{num(telemetry['roll_deg'])}°")
    pitch.metric("Arfagem (pitch)", f"{num(telemetry['pitch_deg'])}°")
    limit_metric.metric("Limite no equipamento", f"{num(telemetry['tilt_limit_deg'])}°")
    temperature = telemetry.get("temp_c")
    humidity = telemetry.get("humidity_pct")
    climate.metric(
        "Temperatura / umidade",
        f"{num(temperature)} °C" if temperature is not None else "—",
        delta=f"{num(humidity, 0)}% de umidade" if humidity is not None else None,
        delta_color="off",
    )


def _tilt_chart(series: dict, limit_deg: float | None) -> None:
    points = series.get("points", [])
    if not points:
        st.caption("Sem leituras nos últimos minutos para desenhar o gráfico.")
        return

    frame = pd.DataFrame(
        [
            {
                "Hora": local_time(point["ts"]),
                "Rolagem": point["roll_deg"],
                "Arfagem": point["pitch_deg"],
            }
            for point in points
        ]
    )
    long_frame = frame.melt("Hora", var_name="Medida", value_name="Graus")
    lines = (
        alt.Chart(long_frame)
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X("Hora:N", sort=None, title=None, axis=alt.Axis(labelOverlap=True)),
            y=alt.Y("Graus:Q", title="graus"),
            color=alt.Color(
                "Medida:N",
                scale=alt.Scale(domain=["Rolagem", "Arfagem"], range=[ROLL_COLOR, PITCH_COLOR]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=["Hora", "Medida", "Graus"],
        )
    )
    layers = [lines]
    if limit_deg is not None:
        limits = pd.DataFrame({"Graus": [limit_deg, -limit_deg]})
        layers.append(
            alt.Chart(limits)
            .mark_rule(color=LIMIT_LINE_COLOR, strokeDash=[5, 4])
            .encode(y="Graus:Q")
        )
    st.altair_chart(alt.layer(*layers).properties(height=240), width="stretch")
    caption = (
        f"Últimos {series.get('minutes', WINDOW_MINUTES)} min · {series.get('total', 0)} leituras"
    )
    if series.get("sampled"):
        caption += " (série amostrada para caber no gráfico; a última leitura é sempre mantida)"
    if limit_deg is not None:
        caption += f" · linhas tracejadas: o limite em uso, ±{num(limit_deg)}°"
    st.caption(caption)


def _event_line(event: dict) -> str:
    icon, description = EVENT_STYLE.get(event["type"], UNKNOWN_EVENT)
    details = []
    if event.get("roll_deg") is not None:
        details.append(f"roll {num(event['roll_deg'])}°")
    if event.get("pitch_deg") is not None:
        details.append(f"pitch {num(event['pitch_deg'])}°")
    if event.get("tilt_limit_deg") is not None:
        details.append(f"limite {num(event['tilt_limit_deg'])}°")
    suffix = f" — {' · '.join(details)}" if details else ""
    return f"{icon} **{local_time(event['ts'])}** · {description}{suffix}"


def _context_chart(event: dict) -> None:
    """Os 30 s antes do evento, que a API devolve em `context` (fields + rows)."""
    context = event.get("context") or {}
    fields, rows = context.get("fields"), context.get("rows")
    if not fields or not rows:
        st.caption("Este evento veio sem o contexto de 30 s.")
        return

    frame = pd.DataFrame(rows, columns=fields)
    value_columns = [column for column in ("roll_deg", "pitch_deg", "accel_g") if column in frame]
    if "t_s" not in frame or not value_columns:
        st.caption("Contexto do evento em formato inesperado.")
        return

    labels = {"roll_deg": "Rolagem (°)", "pitch_deg": "Arfagem (°)", "accel_g": "Aceleração (g)"}
    long_frame = (
        frame[["t_s", *value_columns]]
        .melt("t_s", var_name="Medida", value_name="Valor")
        .replace({"Medida": labels})
    )
    chart = (
        alt.Chart(long_frame)
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X("t_s:Q", title="segundos antes do evento"),
            y=alt.Y("Valor:Q", title=None),
            color=alt.Color("Medida:N", legend=alt.Legend(title=None, orient="top")),
            tooltip=["t_s", "Medida", "Valor"],
        )
        .properties(height=200)
    )
    st.altair_chart(chart, width="stretch")


def _optional(value: float | None, suffix: str) -> str:
    """Valor formatado, ou um travessão: um alerta de capotamento não pode inventar número."""
    return f"{num(value)}{suffix}" if value is not None else "—"


def _rollover_alert(events: list[dict]) -> None:
    """Capotamento recente: alerta fixo no topo com o contexto de 30 s."""
    for event in events:
        if event["type"] != "rollover":
            continue
        age = seconds_since(event.get("received_at") or event.get("ts"))
        if age is None or age > ROLLOVER_HIGHLIGHT_SECONDS:
            return  # os eventos vêm do mais recente para o mais antigo
        st.error(
            f"🚨 **CAPOTAMENTO detectado às {local_time(event['ts'])}** — "
            f"roll {_optional(event.get('roll_deg'), '°')}, "
            f"aceleração {_optional(event.get('accel_g'), ' g')}. "
            "Acione o socorro e verifique o operador.",
            icon="🚨",
        )
        _context_chart(event)
        return


@st.fragment(run_every=LIVE_REFRESH)
def _live_panel(device_id: str) -> None:
    """Bloco ao vivo: só ele se atualiza a cada 2 s, o resto da página fica parado."""
    try:
        status = get_device_status(device_id)
        events = get_device_events(device_id, limit=EVENT_LIMIT)
        series = get_telemetry_series(device_id, minutes=WINDOW_MINUTES)
    except ApiError as error:
        st.error(str(error))
        return

    try:
        telemetry = get_latest_telemetry(device_id)
    except NotFoundError:
        telemetry = None  # ainda não publicou nada: é espera, não erro
    except ApiError as error:
        st.error(str(error))
        return

    _rollover_alert(events)
    _status_band(status)

    if telemetry is None:
        st.info(
            "Aguardando o equipamento conectar… Assim que o ESP32 publicar a primeira "
            "telemetria, os números aparecem aqui sozinhos.",
            icon="⏳",
        )
    else:
        _alert_banner(telemetry)
        _telemetry_metrics(telemetry)
        st.caption(f"Última leitura às {local_time(telemetry['ts'])} (seq {telemetry['seq']}).")

    _tilt_chart(series, telemetry["tilt_limit_deg"] if telemetry else None)

    st.markdown("**Eventos recentes**")
    if not events:
        st.caption("Nenhum evento registrado ainda.")
        return
    for event in events:
        st.markdown(_event_line(event))


# ---------------------------------------------------------------- W11: passaporte (prévia)


def _history_period(history: dict) -> str:
    first, last = history.get("first_seen_at"), history.get("last_seen_at")
    if not first or not last:
        return "Sem leituras no período."
    return f"Da primeira leitura às {local_time(first)} até a última às {local_time(last)}."


def _render_history_section(device_id: str) -> None:
    st.subheader("🪪 Passaporte (prévia)")
    st.caption(
        "O que aconteceu com esta máquina no período — a semente do Passaporte Digital, que "
        "acompanharia o equipamento na renovação, no sinistro e na revenda."
    )

    days = st.slider(
        "Período (dias)",
        min_value=1,
        max_value=MAX_HISTORY_DAYS,
        value=DEFAULT_HISTORY_DAYS,
        key="history_days",
    )

    try:
        history = get_device_history(device_id, days=days)
    except ApiError as error:
        st.error(str(error))
        return

    first, second, third, fourth = st.columns(4)
    first.metric("Horas operando", f"{num(history['operating_hours'])} h")
    max_roll = history.get("max_roll_deg")
    second.metric(
        "Inclinação máxima",
        f"{num(max_roll)}°" if max_roll is not None else "—",
        help="Maior rolagem em módulo registrada no período.",
    )
    third.metric("Tempo acima do limite", f"{num(history['pct_time_above_limit'])}%")
    fourth.metric("Leituras recebidas", f"{history['readings']}")

    fifth, sixth, seventh, eighth = st.columns(4)
    fifth.metric("Alertas de inclinação", f"{history['alerts']}")
    sixth.metric("Capotamentos", f"{history['rollovers']}")
    seventh.metric("Ocorrências do operador", f"{history['incident_reports']}")
    eighth.metric("Limites aplicados", f"{history['limits_applied']}")

    st.caption(_history_period(history))

    st.markdown("**Linha do tempo**")
    timeline = history.get("timeline") or []
    if not timeline:
        st.info(
            "Ainda sem histórico para este equipamento. Assim que ele publicar telemetria e "
            "eventos, eles aparecem aqui.",
            icon="⏳",
        )
    else:
        # Mesma forma de evento de `/events`: reaproveita a linha do painel ao vivo.
        for event in timeline:
            st.markdown(_event_line(event))

    # O que este histórico **ainda não** é — texto da API, exibido como veio (W11).
    st.info(history["roadmap_note"], icon="🗺️")


# ---------------------------------------------------------------- página


st.title("📡 Equipamento ao vivo")

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

selected_device = device_picker(farm_detail)
if selected_device is None:
    st.stop()

st.caption(
    f"{selected_device['name']} · `{selected_device['device_id']}` · "
    f"{farm_detail['name']} ({farm_detail['municipality']}/{farm_detail['state']})"
)

_render_limit_section(farm_detail, selected_device)

st.divider()
st.subheader("📈 Ao vivo")
st.caption("Este bloco se atualiza sozinho a cada 2 segundos.")
_live_panel(selected_device["device_id"])

st.divider()
_render_history_section(selected_device["device_id"])
