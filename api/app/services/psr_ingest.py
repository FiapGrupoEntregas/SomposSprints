"""Ingestão das apólices do PSR/SISSER (D1).

Pipeline explícito, uma função por etapa: **extrair → validar → limpar → transformar → carregar**.

    read_psr(path)      extrai o CSV já sem as colunas pessoais
    clean(df)           valida, limpa e transforma; devolve o que sobrou + o relatório de qualidade
    load_to_db(df, s)   carrega na tabela `policy`
    ingest_file(...)    junta tudo, em blocos, para os arquivos grandes

Nada é descartado em silêncio: todo descarte é contado por motivo em `QualityReport`.

⚠️ **LGPD (ADR-011):** `NM_SEGURADO` e `NR_DOCUMENTO_SEGURADO` **não são lidos**. `read_psr` passa
uma lista branca de colunas para o `pandas`, então os dados pessoais não chegam nem a virar
`DataFrame` — muito menos ao banco ou ao repositório.

Fonte, licença e limitações: document/dados-e-modelo.md §1.
"""

import logging
import unicodedata
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from sqlmodel import Session, select

from app.models import Policy

logger = logging.getLogger(__name__)

# --- Contrato do arquivo (dados-e-modelo.md §1, conferido em 19/09/2026) -----------------------

CSV_SEPARATOR = ";"
CSV_ENCODING = "ISO-8859-1"
DATE_FORMAT = "%d/%m/%Y"
# Coluna vazia no SISSER vem como "-", não como string vazia.
MISSING_MARKERS = ("-", "", "NULL", "null")

#: Colunas com dado pessoal. Nunca entram na leitura (ADR-011). O teste
#: `test_psr_ingest.py::test_colunas_pessoais_nunca_chegam_ao_banco` falha se isso mudar.
PERSONAL_COLUMNS = frozenset({"NM_SEGURADO", "NR_DOCUMENTO_SEGURADO"})

#: Lista branca do que é lido. Tudo que não está aqui é descartado na extração.
SOURCE_COLUMNS = (
    "NM_RAZAO_SOCIAL",
    "ID_PROPOSTA",
    "DT_INICIO_VIGENCIA",
    "DT_FIM_VIGENCIA",
    "NM_MUNICIPIO_PROPRIEDADE",
    "SG_UF_PROPRIEDADE",
    # Coordenada em grau/minuto/segundo: é o que 89% das linhas de 2016–2024 trazem.
    "LATITUDE",
    "NR_GRAU_LAT",
    "NR_MIN_LAT",
    "NR_SEG_LAT",
    "LONGITUDE",
    "NR_GRAU_LONG",
    "NR_MIN_LONG",
    "NR_SEG_LONG",
    # Coordenada decimal: é o que 2025 traz.
    "NR_DECIMAL_LATITUDE",
    "NR_DECIMAL_LONGITUDE",
    "NM_CULTURA_GLOBAL",
    "NR_AREA_TOTAL",
    "VL_LIMITE_GARANTIA",
    "VL_PREMIO_LIQUIDO",
    "ANO_APOLICE",
    "CD_GEOCMU",
    "VALOR_INDENIZAÇÃO",  # o nome da coluna tem acento mesmo
    "EVENTO_PREPONDERANTE",
)

# --- Faixas válidas -----------------------------------------------------------------------------

# Caixa que envolve o Brasil continental, com folga. Fora disso a coordenada está errada.
BRAZIL_LAT_MIN, BRAZIL_LAT_MAX = -34.0, 5.5
BRAZIL_LON_MIN, BRAZIL_LON_MAX = -74.5, -34.0

# Vigência de uma safra. Acima disso a data está trocada ou digitada errada.
MAX_COVERAGE_DAYS = 1100

#: Ressalvas: a linha **é mantida**, mas tem um defeito que limita o uso. Diferente de `discarded`
#: (a linha sai) e de `corrections` (o valor é consertado): aqui o dado fica como veio, e o
#: relatório avisa para quem for consumir.
FLAG_EXPLANATIONS: dict[str, str] = {
    "vigencia_de_um_dia": (
        "início = fim, então não há janela climática. Sem uso para features de período "
        "(D2/D3), que excluem estas linhas; seguem válidas para análise espacial e de evento."
    ),
}
MIN_POLICY_YEAR, MAX_POLICY_YEAR = 2006, 2100

SOUTHERN_OR_WESTERN = frozenset({"S", "W", "O"})

# --- Normalização do evento ---------------------------------------------------------------------

EVENT_NO_CLAIM = "sem_sinistro"
EVENT_OTHER = "outros"

#: Vocabulário de `EVENTO_PREPONDERANTE`. A chave é o texto já normalizado por `event_key`
#: (maiúsculas, sem acento, apóstrofe reta, sem espaço sobrando).
#:
#: **16 valores distintos** foram observados nos três arquivos (contagem de 19/09/2026, união de
#: 2006–2015, 2016–2024 e 2025). As três entradas marcadas como *sinônimo defensivo* **não
#: ocorrem** em nenhum arquivo: estão aqui para o caso de o Mapa mudar o texto, e são a razão de o
#: dicionário ter 19 chaves para 16 valores observados.
EVENT_MAP: dict[str, str] = {
    "SECA": "seca",
    "ESTIAGEM": "seca",  # sinônimo defensivo — não observado
    "CHUVA EXCESSIVA": "chuva_excessiva",
    "INUNDACAO/TROMBA D'AGUA": "chuva_excessiva",
    "INUNDACAO": "chuva_excessiva",  # sinônimo defensivo — não observado
    "GRANIZO": "granizo",
    "GEADA": "geada",
    "VENTOS FORTES/FRIOS": "vento",
    "VENDAVAL": "vento",  # sinônimo defensivo — não observado
    "RAIO": "outros",
    "INCENDIO": "outros",
    "MORTE": "outros",
    "QUEDA DE PARREIRAL": "outros",
    "VARIACAO EXCESSIVA DE TEMPERATURA": "outros",
    "VARIACAO DE PRECO": "outros",
    "DOENCAS E PRAGAS": "outros",
    "PERDA DE QUALIDADE": "outros",
    "REPLANTIO": "outros",
    "DEMAIS CAUSAS": "outros",
}

#: Todas as categorias possíveis. `event_category` nunca sai daqui.
EVENT_CATEGORIES = frozenset(EVENT_MAP.values()) | {EVENT_NO_CLAIM, EVENT_OTHER}


@dataclass
class QualityReport:
    """Relatório de qualidade de uma ingestão. Nada de descarte silencioso."""

    rows_read: int = 0
    rows_kept: int = 0
    #: Linhas que `load_to_db` realmente gravou. Menor que `rows_kept` quando a proposta já estava
    #: no banco (recarga idempotente). Fica `None` num relatório de `clean()`, que não toca o banco
    #: — assim ninguém confunde "não carregado" com "carregou zero".
    rows_loaded: int | None = None
    discarded: Counter[str] = field(default_factory=Counter)
    #: Valores corrigidos no lugar de descartar a linha inteira (ex.: área 0 vira nulo).
    corrections: Counter[str] = field(default_factory=Counter)
    #: Linhas **mantidas** que carregam um defeito conhecido (ver `FLAG_EXPLANATIONS`).
    flags: Counter[str] = field(default_factory=Counter)
    coordinate_source: Counter[str] = field(default_factory=Counter)
    by_event: Counter[str] = field(default_factory=Counter)
    by_state: Counter[str] = field(default_factory=Counter)
    #: Textos de `EVENTO_PREPONDERANTE` que não estavam no vocabulário e caíram em `outros`.
    unknown_events: Counter[str] = field(default_factory=Counter)
    rows_with_indemnity: int = 0

    @property
    def rows_discarded(self) -> int:
        return sum(self.discarded.values())

    @property
    def indemnity_rate(self) -> float:
        """Fração das linhas mantidas que teve indenização paga (> 0)."""
        return self.rows_with_indemnity / self.rows_kept if self.rows_kept else 0.0

    def merge(self, other: "QualityReport") -> None:
        """Soma outro relatório neste (usado para juntar os blocos de um arquivo grande)."""
        self.rows_read += other.rows_read
        self.rows_kept += other.rows_kept
        if other.rows_loaded is not None:
            self.rows_loaded = (self.rows_loaded or 0) + other.rows_loaded
        self.rows_with_indemnity += other.rows_with_indemnity
        for name in (
            "discarded",
            "corrections",
            "flags",
            "coordinate_source",
            "by_event",
            "by_state",
            "unknown_events",
        ):
            getattr(self, name).update(getattr(other, name))

    def as_dict(self) -> dict:
        return {
            "rows_read": self.rows_read,
            "rows_kept": self.rows_kept,
            "rows_loaded": self.rows_loaded,
            "rows_already_in_db": (
                None if self.rows_loaded is None else self.rows_kept - self.rows_loaded
            ),
            "rows_discarded": self.rows_discarded,
            "discarded": dict(self.discarded.most_common()),
            "corrections": dict(self.corrections.most_common()),
            "flags": dict(self.flags.most_common()),
            "coordinate_source": dict(self.coordinate_source.most_common()),
            "rows_with_indemnity": self.rows_with_indemnity,
            "indemnity_rate": round(self.indemnity_rate, 4),
            "by_event": dict(self.by_event.most_common()),
            "by_state": dict(self.by_state.most_common()),
            "unknown_events": dict(self.unknown_events.most_common()),
        }

    def render(self) -> str:
        """Relatório em texto, para o log do script de carga."""
        lines = [
            "Relatório de qualidade — PSR/SISSER",
            f"  linhas lidas .......... {self.rows_read}",
            f"  linhas mantidas ....... {self.rows_kept}",
            f"  linhas descartadas .... {self.rows_discarded}",
        ]
        lines += [f"      {reason}: {count}" for reason, count in self.discarded.most_common()]
        if self.rows_loaded is not None:
            lines.append(f"  linhas gravadas ....... {self.rows_loaded}")
            if self.rows_loaded != self.rows_kept:
                lines.append(
                    f"      ⚠️  {self.rows_kept - self.rows_loaded} já estavam no banco "
                    "(proposta repetida); nada foi sobrescrito"
                )
        if self.corrections:
            lines.append("  valores corrigidos ....")
            lines += [f"      {k}: {v}" for k, v in self.corrections.most_common()]
        if self.flags:
            lines.append("  ⚠️  mantidas com ressalva:")
            for name, count in self.flags.most_common():
                share = f" ({count / self.rows_kept:.1%} das mantidas)" if self.rows_kept else ""
                lines.append(f"      {name}: {count}{share}")
                explanation = FLAG_EXPLANATIONS.get(name)
                if explanation:
                    lines.append(f"        ↳ {explanation}")
        lines.append(
            f"  com indenização ....... {self.rows_with_indemnity} "
            f"({self.indemnity_rate:.1%} das mantidas)"
        )
        lines.append("  origem da coordenada ..")
        lines += [f"      {k}: {v}" for k, v in self.coordinate_source.most_common()]
        lines.append("  por evento ............")
        lines += [f"      {k}: {v}" for k, v in self.by_event.most_common()]
        lines.append("  por UF ................")
        lines += [f"      {k}: {v}" for k, v in self.by_state.most_common()]
        if self.unknown_events:
            lines.append("  evento não reconhecido (foi para 'outros'):")
            lines += [f"      {k!r}: {v}" for k, v in self.unknown_events.most_common()]
        return "\n".join(lines)


# --- 1. Extrair -----------------------------------------------------------------------------


def _read_kwargs(path: Path) -> dict:
    header = pd.read_csv(path, sep=CSV_SEPARATOR, encoding=CSV_ENCODING, nrows=0).columns
    missing = [column for column in SOURCE_COLUMNS if column not in header]
    if missing:
        raise ValueError(f"{path.name}: faltam colunas esperadas do SISSER: {missing}")
    leaked = PERSONAL_COLUMNS - set(header)
    if leaked != PERSONAL_COLUMNS:
        # Só um aviso: o arquivo tem as colunas pessoais, como esperado — e nós não as lemos.
        logger.debug("%s traz colunas pessoais; elas não serão lidas (ADR-011)", path.name)
    return {
        "sep": CSV_SEPARATOR,
        "encoding": CSV_ENCODING,
        # Lista branca: os dados pessoais não chegam nem a virar DataFrame.
        "usecols": list(SOURCE_COLUMNS),
        # Tudo como texto; a conversão de tipo é explícita em `clean`.
        "dtype": str,
        "na_values": list(MISSING_MARKERS),
        "keep_default_na": False,
    }


def read_psr(path: str | Path) -> pd.DataFrame:
    """Lê um CSV do SISSER inteiro, já sem as colunas pessoais (ADR-011).

    Para os arquivos grandes (2006–2015 e 2016–2024, centenas de MB), use `iter_psr` em vez desta.
    """
    path = Path(path)
    return pd.read_csv(path, **_read_kwargs(path))


def iter_psr(path: str | Path, chunk_size: int = 100_000) -> Iterator[pd.DataFrame]:
    """Lê um CSV do SISSER em blocos, para caber na memória (plano B da D1)."""
    path = Path(path)
    yield from pd.read_csv(path, chunksize=chunk_size, **_read_kwargs(path))


# --- 2. Transformações de campo ---------------------------------------------------------------


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def event_key(text: str) -> str:
    """Chave de busca no `EVENT_MAP`: maiúsculas, sem acento, sem espaço sobrando.

    A apóstrofe tem de ser trocada **antes** de remover os acentos: `´` (U+00B4) se decompõe em
    espaço + acento combinante, e a troca depois disso não encontraria mais nada.
    """
    normalized = text.replace("\u00b4", "'").replace("`", "'").replace("\u2019", "'")
    return " ".join(_strip_accents(normalized).upper().split())


def _is_missing(value: object) -> bool:
    """`True` para `None`, `NaN`, `pd.NA` e `pd.NaT`.

    Testar só `isinstance(value, float)` deixava `pd.NA` e `pd.NaT` passarem, e `str(pd.NA)` é
    `"<NA>"` — que não está em `MISSING_MARKERS` e cairia em `outros`. Com uma coluna de dtype
    `string` (que D2 e D3 vão usar), isso transformaria **toda apólice sem sinistro** no rótulo
    errado.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        # `pd.isna` devolve array para entrada não escalar: aí não é valor ausente.
        return False


def normalize_event(text: object) -> str:
    """Normaliza `EVENTO_PREPONDERANTE` em uma das `EVENT_CATEGORIES`.

    Vazio vira `sem_sinistro`; texto fora do vocabulário vira `outros` (e quem chama registra no
    log, via `QualityReport.unknown_events`).
    """
    if _is_missing(text):
        return EVENT_NO_CLAIM
    raw = str(text).strip()
    if raw in MISSING_MARKERS:
        return EVENT_NO_CLAIM
    return EVENT_MAP.get(event_key(raw), EVENT_OTHER)


def _to_float(series: pd.Series) -> pd.Series:
    """Converte número brasileiro (`1.234,56`) em float. O que não converter vira `NaN`."""
    text = series.astype("string").str.strip()
    text = text.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(text, errors="coerce")


def _to_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format=DATE_FORMAT, errors="coerce")


def _dms_to_decimal(
    degrees: pd.Series, minutes: pd.Series, seconds: pd.Series, hemisphere: pd.Series
) -> pd.Series:
    """Converte grau/minuto/segundo em grau decimal, com o sinal dado pelo hemisfério."""
    value = (
        pd.to_numeric(degrees, errors="coerce")
        + pd.to_numeric(minutes, errors="coerce").fillna(0) / 60
        + pd.to_numeric(seconds, errors="coerce").fillna(0) / 3600
    )
    southern_or_western = (
        hemisphere.astype("string").str.strip().str.upper().isin(SOUTHERN_OR_WESTERN)
    )
    return value.where(~southern_or_western, -value)


def _resolve_coordinates(df: pd.DataFrame) -> pd.DataFrame:
    """Resolve `lat`/`lon` e registra de onde vieram.

    A coluna decimal tem prioridade; quando falta (a maioria das linhas até 2024), a coordenada é
    reconstruída de grau/minuto/segundo.
    """
    decimal_lat = _to_float(df["NR_DECIMAL_LATITUDE"])
    decimal_lon = _to_float(df["NR_DECIMAL_LONGITUDE"])
    dms_lat = _dms_to_decimal(df["NR_GRAU_LAT"], df["NR_MIN_LAT"], df["NR_SEG_LAT"], df["LATITUDE"])
    dms_lon = _dms_to_decimal(
        df["NR_GRAU_LONG"], df["NR_MIN_LONG"], df["NR_SEG_LONG"], df["LONGITUDE"]
    )

    has_decimal = decimal_lat.notna() & decimal_lon.notna()
    out = pd.DataFrame(index=df.index)
    out["lat"] = decimal_lat.where(has_decimal, dms_lat)
    out["lon"] = decimal_lon.where(has_decimal, dms_lon)
    out["coordinate_source"] = pd.Series("dms", index=df.index).where(~has_decimal, "decimal")
    return out


# --- 3. Validar, limpar e transformar ----------------------------------------------------------


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, QualityReport]:
    """Valida, limpa e transforma o bruto do SISSER no formato da tabela `policy`.

    Devolve as linhas aproveitáveis e o relatório com a contagem de cada descarte.
    """
    report = QualityReport(rows_read=len(df))
    if df.empty:
        return _empty_frame(), report

    work = pd.DataFrame(index=df.index)
    work["proposal_id"] = df["ID_PROPOSTA"].astype("string").str.strip()
    work["insurer"] = df["NM_RAZAO_SOCIAL"].astype("string").str.strip()
    work["municipality"] = df["NM_MUNICIPIO_PROPRIEDADE"].astype("string").str.strip()
    work["state"] = df["SG_UF_PROPRIEDADE"].astype("string").str.strip().str.upper()
    work["geocode_ibge"] = df["CD_GEOCMU"].astype("string").str.strip()
    work["crop"] = df["NM_CULTURA_GLOBAL"].astype("string").str.strip()
    work[["lat", "lon", "coordinate_source"]] = _resolve_coordinates(df)
    work["area_ha"] = _to_float(df["NR_AREA_TOTAL"])
    work["coverage_value"] = _to_float(df["VL_LIMITE_GARANTIA"])
    work["premium"] = _to_float(df["VL_PREMIO_LIQUIDO"])
    work["start_date"] = _to_date(df["DT_INICIO_VIGENCIA"])
    work["end_date"] = _to_date(df["DT_FIM_VIGENCIA"])
    work["policy_year"] = pd.to_numeric(df["ANO_APOLICE"], errors="coerce")
    work["indemnity_value"] = _to_float(df["VALOR_INDENIZAÇÃO"])
    work["event_category"] = df["EVENTO_PREPONDERANTE"].map(normalize_event)

    # Textos de evento que caíram em `outros` sem estar no vocabulário: precisam aparecer no log.
    raw_event = df["EVENTO_PREPONDERANTE"].astype("string").str.strip()
    unrecognized = raw_event[(work["event_category"] == EVENT_OTHER) & raw_event.notna()]
    for text in unrecognized:
        if event_key(text) not in EVENT_MAP:
            report.unknown_events[text] += 1
    if report.unknown_events:
        logger.warning(
            "EVENTO_PREPONDERANTE não reconhecido (foi para 'outros'): %s",
            dict(report.unknown_events.most_common(10)),
        )

    keep = pd.Series(True, index=work.index)

    def drop(reason: str, bad: pd.Series) -> None:
        """Marca linhas para descarte e conta o motivo (só conta quem ainda estava de pé)."""
        nonlocal keep
        hit = keep & bad.fillna(True)
        count = int(hit.sum())
        if count:
            report.discarded[reason] += count
        keep = keep & ~hit

    drop("sem_id_proposta", work["proposal_id"].isna() | (work["proposal_id"] == ""))
    drop("sem_coordenada", work["lat"].isna() | work["lon"].isna())
    drop(
        "coordenada_fora_do_brasil",
        ~work["lat"].between(BRAZIL_LAT_MIN, BRAZIL_LAT_MAX)
        | ~work["lon"].between(BRAZIL_LON_MIN, BRAZIL_LON_MAX),
    )
    drop("sem_uf", work["state"].isna() | (work["state"].str.len() != 2))
    drop("sem_data_de_vigencia", work["start_date"].isna() | work["end_date"].isna())
    drop("vigencia_invertida", work["end_date"] < work["start_date"])
    drop(
        "vigencia_longa_demais",
        (work["end_date"] - work["start_date"]).dt.days > MAX_COVERAGE_DAYS,
    )
    drop(
        "ano_fora_de_faixa",
        work["policy_year"].isna() | ~work["policy_year"].between(MIN_POLICY_YEAR, MAX_POLICY_YEAR),
    )
    # Área 0 aparece em ~2% das apólices (pecuária e florestas usam NR_ANIMAL, não NR_AREA_TOTAL).
    # Descartar a linha inteira por causa de um campo opcional jogaria fora sinistro bom: o valor
    # vira nulo e a correção é contada.
    not_positive_area = work["area_ha"].notna() & (work["area_ha"] <= 0)
    if int(not_positive_area.sum()):
        report.corrections["area_nao_positiva_virou_nulo"] += int(not_positive_area.sum())
        work.loc[not_positive_area, "area_ha"] = pd.NA

    drop("valor_negativo", _any_negative(work, ("coverage_value", "premium", "indemnity_value")))

    # Mantém a primeira ocorrência **válida** de cada proposta; a tabela tem UNIQUE em
    # `proposal_id`. A duplicidade é calculada só entre as linhas que sobreviveram: se a primeira
    # ocorrência já foi descartada (por falta de coordenada, por exemplo), a segunda é a única que
    # existe e tem de entrar — calcular sobre o frame inteiro jogaria as duas fora.
    survivors = work.loc[keep, "proposal_id"]
    repeated = survivors.duplicated(keep="first").reindex(work.index, fill_value=False)
    drop("duplicada_id_proposta", repeated)

    # Ressalva (não é descarte): o PSR de 2006–2015 traz `DT_INICIO_VIGENCIA` igual a
    # `DT_FIM_VIGENCIA` em **todas** as linhas. A apólice é real e tem coordenada, cultura e
    # evento válidos, então mantê-la é o certo para a ingestão — mas quem for montar feature de
    # período precisa saber que ela não tem janela climática. Achado na D2.
    single_day = keep & (work["end_date"] == work["start_date"])
    if int(single_day.sum()):
        report.flags["vigencia_de_um_dia"] += int(single_day.sum())

    clean_df = work[keep].copy()
    clean_df["policy_year"] = clean_df["policy_year"].astype("Int64")
    clean_df["start_date"] = clean_df["start_date"].dt.date
    clean_df["end_date"] = clean_df["end_date"].dt.date

    report.rows_kept = len(clean_df)
    report.coordinate_source.update(clean_df["coordinate_source"])
    report.by_event.update(clean_df["event_category"])
    report.by_state.update(clean_df["state"].dropna())
    report.rows_with_indemnity = int((clean_df["indemnity_value"].fillna(0) > 0).sum())
    return clean_df, report


def _any_negative(df: pd.DataFrame, columns: tuple[str, ...]) -> pd.Series:
    negative = pd.Series(False, index=df.index)
    for column in columns:
        negative = negative | (df[column].notna() & (df[column] < 0))
    return negative


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "proposal_id",
            "insurer",
            "municipality",
            "state",
            "geocode_ibge",
            "lat",
            "lon",
            "coordinate_source",
            "crop",
            "area_ha",
            "coverage_value",
            "premium",
            "start_date",
            "end_date",
            "policy_year",
            "indemnity_value",
            "event_category",
        ]
    )


# --- 4. Carregar ---------------------------------------------------------------------------------


def _optional(value: object) -> object | None:
    """`NaN`/`NaT`/`pd.NA` do pandas viram `None`, que é o que o SQLite entende."""
    return None if value is None or pd.isna(value) else value


def to_policies(df: pd.DataFrame) -> list[Policy]:
    """Converte o `DataFrame` limpo em objetos `Policy`."""
    return [
        Policy(
            proposal_id=str(row["proposal_id"]),
            insurer=_optional(row["insurer"]),
            municipality=_optional(row["municipality"]),
            state=_optional(row["state"]),
            geocode_ibge=_optional(row["geocode_ibge"]),
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            coordinate_source=str(row["coordinate_source"]),
            crop=_optional(row["crop"]),
            area_ha=_optional(row["area_ha"]),
            coverage_value=_optional(row["coverage_value"]),
            premium=_optional(row["premium"]),
            start_date=_optional(row["start_date"]),
            end_date=_optional(row["end_date"]),
            policy_year=_optional(row["policy_year"]),
            indemnity_value=_optional(row["indemnity_value"]),
            event_category=str(row["event_category"]),
        )
        for _, row in df.iterrows()
    ]


def load_to_db(df: pd.DataFrame, session: Session) -> int:
    """Grava as linhas limpas na tabela `policy` e devolve quantas entraram.

    Propostas que já estão no banco são ignoradas, então recarregar o mesmo arquivo é idempotente.
    """
    if df.empty:
        return 0
    existing = _existing_proposal_ids(session, df["proposal_id"].astype(str).tolist())
    novos = df[~df["proposal_id"].astype(str).isin(existing)]
    policies = to_policies(novos)
    if not policies:
        return 0
    session.add_all(policies)
    session.commit()
    return len(policies)


def _existing_proposal_ids(session: Session, proposal_ids: list[str]) -> set[str]:
    """Consulta em lotes: o SQLite tem limite de variáveis por `IN (...)`."""
    batch = 500
    found: set[str] = set()
    for start in range(0, len(proposal_ids), batch):
        chunk = proposal_ids[start : start + batch]
        rows = session.exec(
            select(Policy.proposal_id).where(Policy.proposal_id.in_(chunk))  # type: ignore[attr-defined]
        ).all()
        found.update(rows)
    return found


def drop_seen_proposals(df: pd.DataFrame, seen: set[str]) -> tuple[pd.DataFrame, int]:
    """Tira as propostas já vistas em blocos anteriores e devolve quantas saíram.

    `clean` só enxerga o bloco que recebeu, então a duplicata que cai em dois blocos diferentes
    passaria despercebida no relatório (o banco a barraria pelo UNIQUE, mas em silêncio).
    """
    if df.empty:
        return df, 0
    proposal_ids = df["proposal_id"].astype(str)
    duplicated = proposal_ids.isin(seen)
    seen.update(proposal_ids[~duplicated])
    return df[~duplicated], int(duplicated.sum())


def ingest_file(path: str | Path, session: Session, chunk_size: int = 100_000) -> QualityReport:
    """Pipeline inteiro de um arquivo: extrair → validar → limpar → transformar → carregar.

    Lê em blocos para caber na memória (o arquivo de 2016–2024 tem ~300 MB) e devolve um relatório
    de qualidade somado, com a mesma contagem que a leitura de uma vez só daria.
    """
    report = QualityReport()
    seen: set[str] = set()
    for chunk in iter_psr(path, chunk_size=chunk_size):
        clean_df, chunk_report = clean(chunk)
        clean_df, repeated = drop_seen_proposals(clean_df, seen)
        if repeated:
            chunk_report.discarded["duplicada_id_proposta"] += repeated
            chunk_report.rows_kept -= repeated
            # Os agregados por linha são refeitos sem as repetidas. `corrections` e
            # `unknown_events` não: eles descrevem o que veio no **arquivo de entrada**, e a
            # correção aconteceu mesmo que a linha depois saísse por duplicidade.
            chunk_report.coordinate_source = Counter(clean_df["coordinate_source"])
            chunk_report.by_event = Counter(clean_df["event_category"])
            chunk_report.by_state = Counter(clean_df["state"].dropna())
            chunk_report.rows_with_indemnity = int(
                (clean_df["indemnity_value"].fillna(0) > 0).sum()
            )
        chunk_report.rows_loaded = load_to_db(clean_df, session)
        report.merge(chunk_report)

    report.rows_loaded = report.rows_loaded or 0
    if report.rows_loaded != report.rows_kept:
        logger.warning(
            "%s: %d linhas limpas, mas só %d gravadas — %d propostas já estavam no banco",
            Path(path).name,
            report.rows_kept,
            report.rows_loaded,
            report.rows_kept - report.rows_loaded,
        )
    # Quem chama é que imprime o relatório (`scripts/load_psr.py`): logar aqui o duplicaria.
    return report
