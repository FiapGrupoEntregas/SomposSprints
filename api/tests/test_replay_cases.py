"""Valida o catálogo de casos reais do replay (`app/data/replay_cases.json`), curado para a W9.

Só a **estrutura e a coerência** dos dados: o motor do replay (`run_replay`) e as rotas são da W9
e têm os próprios testes. Este arquivo existe porque o JSON é dado curado à mão — e dado curado à
mão apodrece em silêncio se ninguém olhar.
"""

import json
from datetime import date
from pathlib import Path

import pytest

CASES_PATH = Path(__file__).resolve().parent.parent / "app" / "data" / "replay_cases.json"

REQUIRED_FIELDS = frozenset(
    {
        "id",
        "title",
        "date",
        "location",
        "location_precision",
        "municipality",
        "state",
        "machine",
        "description",
        "source_url",
    }
)

#: Valores aceitos, e que aparecem na tela (W9).
PRECISIONS = frozenset({"exato", "aproximado", "municipio"})

#: Caixa do Brasil continental, a mesma da ingestão do PSR.
BRAZIL_LAT = (-34.0, 5.5)
BRAZIL_LON = (-74.5, -34.0)

#: A W9 pede pelo menos 3 casos; entregamos 5.
MIN_CASES = 3

#: Antes disso a Archive API (ERA5) é a fonte, e ela não tem `cape`. Não é erro — é um aviso de
#: que o caso perde o perigo de raio por CAPE (regras-de-risco §5.3).
ARCHIVE_CUTOFF = date(2022, 1, 1)


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def test_tem_casos_suficientes(cases: list[dict]) -> None:
    assert len(cases) >= MIN_CASES


def test_todo_caso_tem_os_campos_da_spec(cases: list[dict]) -> None:
    for case in cases:
        assert set(case) == REQUIRED_FIELDS, f"{case.get('id')}: {set(case) ^ REQUIRED_FIELDS}"


def test_ids_sao_unicos(cases: list[dict]) -> None:
    ids = [case["id"] for case in cases]
    assert len(set(ids)) == len(ids)


def test_datas_sao_validas_e_passadas(cases: list[dict]) -> None:
    """Data futura não tem histórico para consultar (a rota devolve 422)."""
    for case in cases:
        quando = date.fromisoformat(case["date"])
        assert quando <= date.today(), f"{case['id']} está no futuro"


def test_coordenadas_caem_no_brasil(cases: list[dict]) -> None:
    for case in cases:
        lat, lon = case["location"]["lat"], case["location"]["lon"]
        assert BRAZIL_LAT[0] <= lat <= BRAZIL_LAT[1], case["id"]
        assert BRAZIL_LON[0] <= lon <= BRAZIL_LON[1], case["id"]


def test_precisao_da_localizacao_e_declarada(cases: list[dict]) -> None:
    """`location_precision` aparece na tela: não pode faltar nem ter valor inventado."""
    for case in cases:
        assert case["location_precision"] in PRECISIONS, case["id"]


def test_toda_fonte_e_um_link(cases: list[dict]) -> None:
    for case in cases:
        assert case["source_url"].startswith("https://"), case["id"]


def test_uf_tem_duas_letras(cases: list[dict]) -> None:
    for case in cases:
        assert len(case["state"]) == 2 and case["state"].isupper(), case["id"]


def test_descricao_diz_alguma_coisa(cases: list[dict]) -> None:
    """A descrição vai para a tela; uma linha vazia ou genérica não ajuda a banca."""
    for case in cases:
        assert len(case["description"]) >= 60, case["id"]


def test_casos_cobrem_mais_de_um_estado(cases: list[dict]) -> None:
    """Um catálogo de um estado só não sustenta o argumento de generalidade."""
    assert len({case["state"] for case in cases}) >= 2


def test_casos_cobrem_mais_de_um_tipo_de_perigo(cases: list[dict]) -> None:
    """Capotamento, incêndio e raio exercitam regras diferentes (§5.1, §5.3, §5.5)."""
    assert len({case["machine"] for case in cases}) >= 2
