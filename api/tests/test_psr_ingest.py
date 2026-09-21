"""Testes da ingestão do PSR/SISSER (D1). Rodam offline, com os CSVs versionados.

Dois arquivos alimentam estes testes:

- `data/sample/psr_amostra.csv` — 453 linhas **reais** do SISSER, 55 por categoria de evento,
  anonimizadas (`scripts/build_psr_sample.py`).
- `tests/fixtures/psr_sujo.csv` — 14 linhas **sintéticas**, escritas à mão para exercitar cada
  motivo de descarte e a anonimização. Não é dado real e não deve ser usado para estatística.
"""

from collections import Counter
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Policy
from app.services.psr_ingest import (
    CSV_ENCODING,
    CSV_SEPARATOR,
    EVENT_CATEGORIES,
    EVENT_MAP,
    FLAG_EXPLANATIONS,
    PERSONAL_COLUMNS,
    QualityReport,
    clean,
    ingest_file,
    load_to_db,
    normalize_event,
    read_psr,
)

SAMPLE_PATH = Path(__file__).resolve().parents[2] / "data" / "sample" / "psr_amostra.csv"
DIRTY_PATH = Path(__file__).parent / "fixtures" / "psr_sujo.csv"

#: Valor pessoal plantado na linha 9000002 do fixture sujo. Não pode sobreviver ao pipeline.
PLANTED_NAME = "MARIA APARECIDA DA SILVA"
PLANTED_DOCUMENT = "***12345678"


@pytest.fixture
def session() -> Session:
    """Banco em memória, com `StaticPool` para a conexão sobreviver entre as sessões."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(scope="module")
def sample() -> pd.DataFrame:
    return read_psr(SAMPLE_PATH)


@pytest.fixture(scope="module")
def dirty() -> pd.DataFrame:
    return read_psr(DIRTY_PATH)


# --- LGPD (ADR-011) -----------------------------------------------------------------------------


def test_read_psr_nao_carrega_as_colunas_pessoais(dirty: pd.DataFrame) -> None:
    """As colunas pessoais existem no CSV, mas não chegam nem a virar `DataFrame`."""
    raw_header = pd.read_csv(DIRTY_PATH, sep=CSV_SEPARATOR, encoding=CSV_ENCODING, nrows=0).columns
    assert set(raw_header) >= PERSONAL_COLUMNS, "o fixture precisa ter as colunas pessoais"
    assert PERSONAL_COLUMNS.isdisjoint(dirty.columns)


def test_colunas_pessoais_nunca_chegam_ao_banco(session: Session) -> None:
    """Requisito de LGPD: nenhum nome nem documento pode aparecer na tabela `policy`."""
    ingest_file(DIRTY_PATH, session, chunk_size=5)

    assert PERSONAL_COLUMNS.isdisjoint(Policy.model_fields), (
        "a tabela policy ganhou uma coluna com nome de campo pessoal"
    )

    policies = session.exec(select(Policy)).all()
    # Sem esta âncora o teste passaria à toa: se a linha que carrega o nome plantado deixasse de
    # ser ingerida, a varredura abaixo não olharia para nada.
    assert any(policy.proposal_id == "9000002" for policy in policies), (
        "a linha 9000002, que traz o nome plantado no CSV, precisa estar no banco"
    )
    for policy in policies:
        for value in policy.model_dump().values():
            assert PLANTED_NAME not in str(value)
            assert PLANTED_DOCUMENT not in str(value)


def test_amostra_versionada_nao_tem_dado_pessoal() -> None:
    """O CSV que vai para o Git guarda as colunas, mas só com os marcadores de anonimização."""
    raw = pd.read_csv(SAMPLE_PATH, sep=CSV_SEPARATOR, encoding=CSV_ENCODING, dtype=str)
    for column in PERSONAL_COLUMNS:
        assert set(raw[column].unique()) <= {"ANONIMIZADO", "***"}


def test_amostra_versionada_cabe_no_limite() -> None:
    """A amostra fica bem abaixo dos 5 MB do escopo da D1."""
    assert SAMPLE_PATH.stat().st_size < 5 * 1024 * 1024


# --- Leitura: acentos, decimal com vírgula e datas ----------------------------------------------


def test_leitura_preserva_acentos(sample: pd.DataFrame) -> None:
    """ISO-8859-1 lido errado viraria "Jata" ou "JataÃ­"."""
    assert (sample["NM_MUNICIPIO_PROPRIEDADE"] == "Jataí").any()
    assert (sample["NM_CULTURA_GLOBAL"] == "Milho 2ª safra").any()


def test_decimal_com_virgula_vira_float(dirty: pd.DataFrame) -> None:
    """`1.280.124,50` é um milhão e duzentos mil, não 1,28."""
    cleaned, _ = clean(dirty)
    linha = cleaned[cleaned["proposal_id"] == "9000001"].iloc[0]
    assert linha["coverage_value"] == pytest.approx(1_280_124.50)
    assert linha["premium"] == pytest.approx(120_587.38)
    assert linha["lat"] == pytest.approx(-18.109499)


def test_datas_sao_lidas_no_formato_brasileiro(dirty: pd.DataFrame) -> None:
    """`10/10/2022` é 10 de outubro, não 10 de outubro lido como mês 10 por acaso."""
    cleaned, _ = clean(dirty)
    linha = cleaned[cleaned["proposal_id"] == "9000001"].iloc[0]
    assert (linha["start_date"].day, linha["start_date"].month) == (10, 10)
    assert (linha["end_date"].day, linha["end_date"].month) == (20, 3)


# --- Coordenadas --------------------------------------------------------------------------------


def test_coordenada_em_grau_minuto_segundo_e_convertida(dirty: pd.DataFrame) -> None:
    """89% das linhas até 2024 só têm DMS; sem essa conversão, perderíamos quase tudo."""
    cleaned, _ = clean(dirty)
    linha = cleaned[cleaned["proposal_id"] == "9000002"].iloc[0]
    # S 18°03'02" W 50°38'47" — Rio Verde (GO)
    assert linha["lat"] == pytest.approx(-18.0506, abs=1e-4)
    assert linha["lon"] == pytest.approx(-50.6464, abs=1e-4)
    assert linha["coordinate_source"] == "dms"


def test_coordenada_decimal_tem_prioridade(dirty: pd.DataFrame) -> None:
    cleaned, _ = clean(dirty)
    linha = cleaned[cleaned["proposal_id"] == "9000001"].iloc[0]
    assert linha["coordinate_source"] == "decimal"


def test_amostra_real_usa_as_duas_formas_de_coordenada(sample: pd.DataFrame) -> None:
    _, report = clean(sample)
    assert report.coordinate_source["decimal"] > 0
    assert report.coordinate_source["dms"] > 0


# --- Normalização do evento ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        (" SECA", "seca"),
        ("GRANIZO", "granizo"),
        ("GEADA", "geada"),
        (" CHUVA EXCESSIVA", "chuva_excessiva"),
        # O `´` de "D´ÁGUA" se decompõe em espaço + acento: a troca tem de vir antes.
        (" INUNDAÇÃO/TROMBA D´ÁGUA", "chuva_excessiva"),
        ("INUNDAÇÃO/TROMBA D'ÁGUA", "chuva_excessiva"),
        (" VENTOS FORTES/FRIOS", "vento"),
        (" DEMAIS CAUSAS", "outros"),
        (" INCÊNDIO", "outros"),
        ("-", "sem_sinistro"),
        ("", "sem_sinistro"),
        (None, "sem_sinistro"),
        (float("nan"), "sem_sinistro"),
        # `pd.NA` e `pd.NaT` não são `float`: str(pd.NA) == "<NA>" cairia em "outros".
        (pd.NA, "sem_sinistro"),
        (pd.NaT, "sem_sinistro"),
        ("ATAQUE DE JACARÉ", "outros"),
    ],
)
def test_normalize_event(texto: object, esperado: str) -> None:
    assert normalize_event(texto) == esperado


def test_normalize_event_em_coluna_de_dtype_string() -> None:
    """D2 e D3 leem com dtype `string`, onde o vazio é `pd.NA`, não `float("nan")`.

    Se a guarda de nulo não pegar `pd.NA`, **toda apólice sem sinistro** vira `outros` — o rótulo
    que a D1 existe para entregar seria corrompido em silêncio.
    """
    coluna = pd.Series([" SECA", pd.NA, " GRANIZO", None], dtype="string")
    assert coluna.map(normalize_event).tolist() == [
        "seca",
        "sem_sinistro",
        "granizo",
        "sem_sinistro",
    ]


def test_todo_o_vocabulario_conhecido_cai_em_uma_categoria_valida() -> None:
    assert set(EVENT_MAP.values()) <= EVENT_CATEGORIES


def test_event_category_cobre_100_por_cento_da_amostra(sample: pd.DataFrame) -> None:
    cleaned, report = clean(sample)
    assert cleaned["event_category"].isin(EVENT_CATEGORIES).all()
    assert cleaned["event_category"].notna().all()
    assert sum(report.by_event.values()) == report.rows_kept


def test_evento_desconhecido_vai_para_outros_e_aparece_no_relatorio(dirty: pd.DataFrame) -> None:
    cleaned, report = clean(dirty)
    linha = cleaned[cleaned["proposal_id"] == "9000003"].iloc[0]
    assert linha["event_category"] == "outros"
    assert report.unknown_events["EVENTO NOVO DO SISSER"] == 1


def test_evento_conhecido_nao_entra_em_unknown_events(sample: pd.DataFrame) -> None:
    """Se o vocabulário estiver correto, a amostra real não gera nenhum desconhecido."""
    _, report = clean(sample)
    assert report.unknown_events == {}


# --- Descartes ------------------------------------------------------------------------------------


def test_cada_motivo_de_descarte_e_contado(dirty: pd.DataFrame) -> None:
    """Nada de `dropna()` silencioso: cada linha ruim do fixture aparece com o seu motivo."""
    _, report = clean(dirty)
    assert dict(report.discarded) == {
        "sem_id_proposta": 1,
        "sem_coordenada": 1,
        "coordenada_fora_do_brasil": 1,
        "sem_uf": 1,
        "sem_data_de_vigencia": 1,
        "vigencia_invertida": 1,
        "vigencia_longa_demais": 1,
        "ano_fora_de_faixa": 1,
        "valor_negativo": 1,
        "duplicada_id_proposta": 1,
    }


def test_o_relatorio_fecha_a_conta(dirty: pd.DataFrame) -> None:
    _, report = clean(dirty)
    assert report.rows_read == report.rows_kept + report.rows_discarded
    assert report.rows_kept == 4


def test_area_zero_vira_nulo_em_vez_de_descartar_a_linha(dirty: pd.DataFrame) -> None:
    """Pecuária e floresta não preenchem `NR_AREA_TOTAL`; perder o sinistro por isso seria pior."""
    cleaned, report = clean(dirty)
    linha = cleaned[cleaned["proposal_id"] == "9000018"].iloc[0]
    assert pd.isna(linha["area_ha"])
    assert report.corrections["area_nao_positiva_virou_nulo"] == 1
    assert "area_invalida" not in report.discarded


def test_duplicada_mantem_a_primeira_ocorrencia(dirty: pd.DataFrame) -> None:
    cleaned, _ = clean(dirty)
    iguais = cleaned[cleaned["proposal_id"] == "9000001"]
    assert len(iguais) == 1
    assert iguais.iloc[0]["municipality"] == "Jataí"


def test_duplicada_cuja_primeira_ocorrencia_e_invalida_nao_mata_a_boa(
    dirty: pd.DataFrame,
) -> None:
    """A 2ª ocorrência tem de entrar quando a 1ª já foi descartada.

    Calcular `duplicated()` sobre o frame inteiro marcaria a linha boa como repetida de uma linha
    que nem existe mais no resultado — e as duas se perderiam.
    """
    # 9000011 é descartada por `sem_coordenada`. Acrescenta uma segunda ocorrência, esta válida.
    valida = dirty[dirty["ID_PROPOSTA"] == "9000001"].head(1).copy()
    valida["ID_PROPOSTA"] = "9000011"
    com_duplicata = pd.concat([dirty, valida], ignore_index=True)

    cleaned, report = clean(com_duplicata)

    assert "9000011" in set(cleaned["proposal_id"]), (
        "a ocorrência válida foi descartada junto com a inválida"
    )
    assert report.discarded["sem_coordenada"] == 1
    # Só a repetição de 9000001, que já existia no fixture.
    assert report.discarded["duplicada_id_proposta"] == 1


def _com_vigencia_de_um_dia(dirty: pd.DataFrame) -> pd.DataFrame:
    """Copia a linha boa 9000001 trocando a data de fim pela de início."""
    linha = dirty[dirty["ID_PROPOSTA"] == "9000001"].head(1).copy()
    linha["ID_PROPOSTA"] = "9000100"
    linha["DT_FIM_VIGENCIA"] = linha["DT_INICIO_VIGENCIA"]
    return pd.concat([dirty, linha], ignore_index=True)


def test_vigencia_de_um_dia_e_mantida_e_sinalizada(dirty: pd.DataFrame) -> None:
    """Achado da D2: **todo** o arquivo de 2006–2015 tem início igual a fim (430.843 linhas).

    A linha é mantida de propósito — a apólice é real, com coordenada, cultura e evento válidos, e
    a ingestão não deve inventar um descarte. Mas o relatório tem de dizer, porque quem montar
    feature de período (D2/D3) não pode usar essas linhas.
    """
    cleaned, report = clean(_com_vigencia_de_um_dia(dirty))

    assert "9000100" in set(cleaned["proposal_id"]), "a linha não pode ser descartada"
    assert report.flags["vigencia_de_um_dia"] == 1
    assert "vigencia_de_um_dia" not in report.discarded
    assert report.rows_read == report.rows_kept + report.rows_discarded


def test_ressalva_aparece_no_relatorio_com_a_consequencia(dirty: pd.DataFrame) -> None:
    """Não basta contar: quem lê o relatório precisa entender o que fazer com o número."""
    _, report = clean(_com_vigencia_de_um_dia(dirty))
    texto = report.render()

    assert "mantidas com ressalva" in texto
    assert "vigencia_de_um_dia: 1" in texto
    assert FLAG_EXPLANATIONS["vigencia_de_um_dia"] in texto
    assert report.as_dict()["flags"] == {"vigencia_de_um_dia": 1}


def test_vigencia_invertida_continua_sendo_descarte(dirty: pd.DataFrame) -> None:
    """Ressalva é para o dado inútil mas íntegro; data invertida continua sendo erro."""
    _, report = clean(dirty)
    assert report.discarded["vigencia_invertida"] == 1
    assert "vigencia_invertida" not in report.flags


def test_ressalvas_somam_entre_blocos(session: Session) -> None:
    """Como os descartes, a contagem tem de fechar na leitura em blocos."""
    total = ingest_file(DIRTY_PATH, session, chunk_size=3)
    assert total.flags == Counter()  # o fixture não tem vigência de um dia

    parcial = QualityReport()
    parcial.flags["vigencia_de_um_dia"] = 2
    outro = QualityReport()
    outro.flags["vigencia_de_um_dia"] = 3
    parcial.merge(outro)
    assert parcial.flags["vigencia_de_um_dia"] == 5


def test_clean_aceita_dataframe_vazio(dirty: pd.DataFrame) -> None:
    cleaned, report = clean(dirty.iloc[0:0])
    assert cleaned.empty
    assert report.rows_read == 0 and report.rows_kept == 0


def test_indemnity_rate_conta_so_valor_positivo(dirty: pd.DataFrame) -> None:
    _, report = clean(dirty)
    # Das 4 linhas mantidas, duas têm indenização paga (9000002 e 9000003).
    assert report.rows_with_indemnity == 2
    assert report.indemnity_rate == pytest.approx(2 / 4)


# --- Carga ----------------------------------------------------------------------------------------


def test_load_to_db_grava_os_campos(session: Session) -> None:
    cleaned, _ = clean(read_psr(DIRTY_PATH))
    assert load_to_db(cleaned, session) == 4

    policy = session.exec(select(Policy).where(Policy.proposal_id == "9000002")).one()
    assert policy.state == "GO"
    assert policy.municipality == "Rio Verde"
    assert policy.crop == "Milho 2ª safra"
    assert policy.event_category == "chuva_excessiva"
    assert policy.indemnity_value == pytest.approx(45_300.25)
    assert policy.policy_year == 2022
    assert policy.created_at is not None


def test_carregar_duas_vezes_nao_duplica(session: Session) -> None:
    """Recarregar o mesmo arquivo é idempotente: a tabela tem UNIQUE em `proposal_id`."""
    ingest_file(DIRTY_PATH, session)
    primeira = len(session.exec(select(Policy)).all())

    ingest_file(DIRTY_PATH, session)
    assert len(session.exec(select(Policy)).all()) == primeira


def test_relatorio_de_clean_nao_finge_ter_tocado_o_banco(dirty: pd.DataFrame) -> None:
    """`clean` não grava nada: `rows_loaded` é `None`, não 0 — senão pareceria carga falhada."""
    _, report = clean(dirty)
    assert report.rows_loaded is None
    assert report.as_dict()["rows_already_in_db"] is None
    assert "linhas gravadas" not in report.render()


def test_relatorio_informa_quantas_linhas_foram_gravadas(session: Session) -> None:
    report = ingest_file(DIRTY_PATH, session)
    assert report.rows_loaded == report.rows_kept == 4
    assert "linhas gravadas ....... 4" in report.render()
    assert report.as_dict()["rows_already_in_db"] == 0


def test_recarga_reporta_zero_gravadas_em_vez_de_silenciar(session: Session) -> None:
    """A 2ª execução do `load_psr.py` não pode dizer "4 mantidas" enquanto grava 0."""
    ingest_file(DIRTY_PATH, session)
    segunda = ingest_file(DIRTY_PATH, session)

    assert segunda.rows_kept == 4
    assert segunda.rows_loaded == 0
    assert segunda.as_dict()["rows_already_in_db"] == 4
    assert "já estavam no banco" in segunda.render()


def test_sem_sinistro_grava_indenizacao_nula(session: Session) -> None:
    ingest_file(DIRTY_PATH, session)
    policy = session.exec(select(Policy).where(Policy.proposal_id == "9000001")).one()
    assert policy.indemnity_value is None
    assert policy.event_category == "sem_sinistro"


def test_ingest_file_em_blocos_da_o_mesmo_relatorio() -> None:
    """Ler em blocos (plano B para o arquivo de 300 MB) não pode mudar o resultado."""
    inteiro, _ = clean(read_psr(DIRTY_PATH))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        report = ingest_file(DIRTY_PATH, session, chunk_size=3)
    assert report.rows_read == 14
    assert report.rows_kept == len(inteiro)


def test_amostra_real_carrega_inteira(session: Session) -> None:
    """O caminho completo com dado real, da leitura à tabela `policy`."""
    report = ingest_file(SAMPLE_PATH, session, chunk_size=200)
    assert report.rows_read == 453
    assert report.rows_loaded == report.rows_kept
    assert report.rows_kept == len(session.exec(select(Policy)).all())
    assert report.by_state["PR"] > 0
    assert set(report.by_event) <= EVENT_CATEGORIES
