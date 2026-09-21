"""Schemas das fazendas de demonstração (W1).

O conteúdo vem de `app/data/farms.json`, validado no boot da API por `app/services/farms.py`.
A geometria (`bbox`) alimenta a grade 10 × 10 do relevo
([document/regras-de-risco.md §1](../../../document/regras-de-risco.md#1-relevo-w2)) e
`reference_tilt_limit_deg` é o `L_ref` do limite dinâmico (§4).
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

# regras-de-risco §4 — limite de referência do equipamento em solo seco; o padrão do projeto é 15°
DEFAULT_REFERENCE_TILT_LIMIT_DEG = 15.0


class LatLon(BaseModel):
    """Um ponto geográfico em graus decimais (WGS84)."""

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90.0, le=90.0, description="Latitude em graus decimais.")
    lon: float = Field(ge=-180.0, le=180.0, description="Longitude em graus decimais.")


class BBox(BaseModel):
    """Retângulo da fazenda. Vira a grade 10 × 10 do relevo (regras-de-risco §1)."""

    model_config = ConfigDict(extra="forbid")

    north: float = Field(ge=-90.0, le=90.0, description="Latitude do lado norte.")
    south: float = Field(ge=-90.0, le=90.0, description="Latitude do lado sul.")
    west: float = Field(ge=-180.0, le=180.0, description="Longitude do lado oeste.")
    east: float = Field(ge=-180.0, le=180.0, description="Longitude do lado leste.")

    @model_validator(mode="after")
    def check_orientation(self) -> "BBox":
        """Garante que o norte fica acima do sul e o leste à direita do oeste."""
        if self.north <= self.south:
            raise ValueError(
                f"bbox inválida: 'north' ({self.north}) precisa ser maior que "
                f"'south' ({self.south})."
            )
        if self.east <= self.west:
            raise ValueError(
                f"bbox inválida: 'east' ({self.east}) precisa ser maior que 'west' ({self.west})."
            )
        return self

    def contains(self, point: LatLon) -> bool:
        """Diz se o ponto está dentro do retângulo (bordas incluídas)."""
        return self.south <= point.lat <= self.north and self.west <= point.lon <= self.east


class Device(BaseModel):
    """Equipamento da fazenda. O `device_id` é o mesmo dos tópicos MQTT (contrato-mqtt.md §1)."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, description="Identificador usado nos tópicos MQTT.")
    name: str = Field(min_length=1, description="Nome exibido na interface.")
    type: str = Field(min_length=1, description="Tipo do equipamento (ex.: tractor, harvester).")
    base_tilt_limit_deg: float = Field(
        gt=0.0,
        le=90.0,
        description="Limite de inclinação do equipamento em solo seco, em graus (L_ref).",
    )


class FarmSummary(BaseModel):
    """Fazenda como aparece no seletor do front (`GET /api/v1/farms`)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, description="Identificador da fazenda (slug).")
    name: str = Field(min_length=1, description="Nome exibido na interface.")
    municipality: str = Field(min_length=1, description="Município.")
    state: str = Field(min_length=2, max_length=2, description="Sigla da unidade federativa.")
    crop: str = Field(min_length=1, description="Cultura principal.")


class Farm(FarmSummary):
    """Fazenda completa (`GET /api/v1/farms/{farm_id}`)."""

    center: LatLon = Field(description="Centro da fazenda; é o ponto usado na previsão do tempo.")
    bbox: BBox = Field(description="Retângulo da fazenda, base da grade 10 × 10 do relevo.")
    reference_tilt_limit_deg: float = Field(
        default=DEFAULT_REFERENCE_TILT_LIMIT_DEG,
        gt=0.0,
        le=90.0,
        description="Limite de inclinação de referência da fazenda em solo seco (regras §4).",
    )
    devices: list[Device] = Field(min_length=1, description="Equipamentos da fazenda.")

    @model_validator(mode="after")
    def check_center_inside_bbox(self) -> "Farm":
        """O centro precisa cair dentro da bbox, senão a previsão e o mapa discordariam."""
        if not self.bbox.contains(self.center):
            raise ValueError(
                f"O centro ({self.center.lat}, {self.center.lon}) está fora da bbox da fazenda."
            )
        return self

    @model_validator(mode="after")
    def check_unique_device_ids(self) -> "Farm":
        """Dois equipamentos com o mesmo `device_id` quebrariam a busca e os tópicos MQTT."""
        seen: set[str] = set()
        for device in self.devices:
            if device.device_id in seen:
                raise ValueError(f"'device_id' repetido na fazenda: '{device.device_id}'.")
            seen.add(device.device_id)
        return self

    def summary(self) -> FarmSummary:
        """Versão resumida, usada na listagem."""
        return FarmSummary(
            id=self.id,
            name=self.name,
            municipality=self.municipality,
            state=self.state,
            crop=self.crop,
        )
