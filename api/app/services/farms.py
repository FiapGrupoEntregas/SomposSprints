"""Catálogo das fazendas de demonstração (W1).

As fazendas ficam num JSON versionado (`app/data/farms.json`), lido **uma vez** e validado com
pydantic. Se o arquivo estiver faltando, malformado ou com um campo errado, a API **não sobe**:
`app/main.py` chama `load_farms()` na criação do app para o erro aparecer no boot, e não no meio
da demo.
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from app.schemas.farm import Device, Farm

# O JSON fica dentro do pacote (`app/data/`), então o caminho não depende do diretório de trabalho.
FARMS_FILE = Path(__file__).resolve().parents[1] / "data" / "farms.json"


class FarmsDataError(RuntimeError):
    """O catálogo de fazendas não pôde ser lido ou está inválido. Levantada no boot da API."""


def load_farms_from_file(path: Path) -> tuple[Farm, ...]:
    """Lê e valida o catálogo do caminho informado. Erros viram `FarmsDataError` em português."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise FarmsDataError(
            f"Não foi possível ler o catálogo de fazendas em '{path}': {error}."
        ) from error

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise FarmsDataError(
            f"O catálogo de fazendas '{path}' não é um JSON válido: {error}."
        ) from error

    if not isinstance(payload, list) or not payload:
        raise FarmsDataError(
            f"O catálogo de fazendas '{path}' precisa ser uma lista com pelo menos uma fazenda."
        )

    farms: list[Farm] = []
    for index, item in enumerate(payload):
        try:
            farms.append(Farm.model_validate(item))
        except ValidationError as error:
            raise FarmsDataError(
                f"Fazenda inválida na posição {index} de '{path}':\n{_describe(error)}"
            ) from error

    _check_unique_farm_ids(farms, path)
    _check_unique_device_ids(farms, path)
    return tuple(farms)


@lru_cache
def load_farms() -> tuple[Farm, ...]:
    """Catálogo completo, lido do disco uma única vez por processo."""
    return load_farms_from_file(FARMS_FILE)


def list_farms() -> tuple[Farm, ...]:
    """Todas as fazendas, na ordem do arquivo."""
    return load_farms()


def get_farm(farm_id: str) -> Farm | None:
    """Fazenda pelo `id`, ou `None` se não existir (a rota transforma isso em 404)."""
    for farm in load_farms():
        if farm.id == farm_id:
            return farm
    return None


def find_device(device_id: str) -> tuple[Farm, Device] | None:
    """Equipamento pelo `device_id`, junto da fazenda dele, ou `None` se não existir.

    O W4 usa isso para descobrir de qual fazenda vem o clima que define o limite do equipamento.
    """
    for farm in load_farms():
        for device in farm.devices:
            if device.device_id == device_id:
                return farm, device
    return None


def _check_unique_farm_ids(farms: list[Farm], path: Path) -> None:
    seen: set[str] = set()
    for farm in farms:
        if farm.id in seen:
            raise FarmsDataError(f"'id' de fazenda repetido em '{path}': '{farm.id}'.")
        seen.add(farm.id)


def _check_unique_device_ids(farms: list[Farm], path: Path) -> None:
    """`device_id` é a chave dos tópicos MQTT, então precisa ser único no catálogo inteiro."""
    seen: set[str] = set()
    for farm in farms:
        for device in farm.devices:
            if device.device_id in seen:
                raise FarmsDataError(f"'device_id' repetido em '{path}': '{device.device_id}'.")
            seen.add(device.device_id)


def _describe(error: ValidationError) -> str:
    """Resume os erros do pydantic em linhas legíveis, apontando o campo com problema."""
    lines = []
    for detail in error.errors():
        field = ".".join(str(part) for part in detail["loc"]) or "(raiz)"
        lines.append(f"  - campo '{field}': {detail['msg']}")
    return "\n".join(lines)
