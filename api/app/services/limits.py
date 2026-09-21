"""Limite de inclinação do dia de um equipamento e o `config` que vai para ele (W4).

O cálculo é o mesmo `regras-de-risco §4` do mapa de risco (W3)
([document/regras-de-risco.md](../../../document/regras-de-risco.md)); o que muda é o `L_ref`:

- **mapa da fazenda** (W3): o **menor** `base_tilt_limit_deg` entre os equipamentos dela;
- **equipamento** (W4, aqui): o `base_tilt_limit_deg` **daquele** equipamento, com fallback no
  `reference_tilt_limit_deg` da fazenda.

O payload publicado segue
[document/contrato-mqtt.md](../../../document/contrato-mqtt.md#config-w4--e3-retained), e o `reason`
sai **sem acento**, porque a fonte padrão do OLED não tem (E7).

Funções puras: quem busca a previsão e quem publica no broker é a rota
(`app/api/v1/routes/devices.py`).
"""

import unicodedata
from datetime import date, datetime, time, timedelta

from app.core.clock import LOCAL_TIMEZONE
from app.schemas.device import DeviceLimit
from app.schemas.farm import Device, Farm
from app.schemas.mqtt import ConfigMessage
from app.schemas.risk import DayRisk, Scenario
from app.services.risk import SOIL_STATE_LABEL, format_number, tilt_limit

# regras-de-risco §4 / E2 — o ESP32 acende 🟡 a partir de 80% do limite
WARN_RATIO = 0.8


def device_reference_tilt_limit_deg(device: Device | None, farm: Farm) -> float:
    """`L_ref` de **um equipamento** (regras-de-risco §4).

    É o `base_tilt_limit_deg` dele; se faltar, o `reference_tilt_limit_deg` da fazenda. Hoje o
    catálogo (`app/schemas/farm.py`) exige o campo no equipamento, então o fallback só vale para
    uma fazenda sem equipamento cadastrado — mas ele é o que o documento manda.

    **Não confundir com `risk.farm_reference_tilt_limit_deg`**, que é o L_ref do *mapa da fazenda*
    e usa o menor limite entre as máquinas (leitura conservadora do W3).
    """
    if device is None:
        return farm.reference_tilt_limit_deg
    return device.base_tilt_limit_deg


def limit_reason(day: DayRisk) -> str:
    """Motivo curto para o display do equipamento, **sem acento** (contrato-mqtt, E7).

    Ex.: `"solo encharcado: 42 mm em 72 h"`.
    """
    rain = format_number(day.rain_72h_mm)
    return strip_accents(f"{SOIL_STATE_LABEL[day.soil_state]}: {rain} mm em 72 h")


def strip_accents(text: str) -> str:
    """Remove os acentos mantendo as letras (`úmido` → `umido`).

    A fonte padrão do OLED não tem acento: sem isto, o display mostra caracteres quebrados e o
    Serial do Wokwi loga lixo.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def end_of_day_epoch(day: date) -> int:
    """Epoch em segundos da **meia-noite que encerra o dia**, no fuso das fazendas.

    É o `valid_until` do contrato: depois disso o ESP32 segue usando o último limite, mas avisa.
    """
    midnight = datetime.combine(day + timedelta(days=1), time.min, tzinfo=LOCAL_TIMEZONE)
    return int(midnight.timestamp())


def compute_device_limit(
    device: Device | None,
    farm: Farm,
    day: DayRisk,
    scenario: Scenario | None = None,
) -> DeviceLimit:
    """Limite do dia daquele equipamento, pronto para a tela e para o `config` (W4).

    Recebe o dia **já calculado** pelo motor de risco (W3), então o estado do solo e o nível da
    fazenda são exatamente os que o mapa mostra.
    """
    reference_deg = device_reference_tilt_limit_deg(device, farm)

    return DeviceLimit(
        device_id=device.device_id if device is not None else farm.devices[0].device_id,
        farm_id=farm.id,
        date=day.date,
        scenario=scenario,
        tilt_limit_deg=tilt_limit(reference_deg, day.soil_state),
        reference_tilt_limit_deg=reference_deg,
        warn_ratio=WARN_RATIO,
        soil_state=day.soil_state,
        risk_level=day.worst_level,
        rain_72h_mm=day.rain_72h_mm,
        wind_max_kmh=day.wind_max_kmh,
        valid_until=end_of_day_epoch(day.date),
        reason=limit_reason(day),
    )


def to_config_message(limit: DeviceLimit) -> ConfigMessage:
    """Converte o limite na mensagem `config` do contrato-mqtt (o que vai para o broker)."""
    return ConfigMessage(
        tilt_limit_deg=limit.tilt_limit_deg,
        warn_ratio=limit.warn_ratio,
        soil_state=limit.soil_state.value,
        risk_level=limit.risk_level.value,
        wind_max_kmh=limit.wind_max_kmh,
        valid_until=limit.valid_until,
        reason=limit.reason,
    )
