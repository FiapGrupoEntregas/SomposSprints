"""Configuração do front-web: o `.env` precisa ser lido de verdade (achado do QA).

`config.py` lê as variáveis **no import**, então cada caso recarrega o módulo com
`importlib.reload` depois de preparar o ambiente. Não é gambiarra: é a única forma de
exercitar o que acontece quando o Streamlit sobe.
"""

import importlib
from pathlib import Path

import dotenv
import pytest

import config

REAL_LOAD_DOTENV = dotenv.load_dotenv


@pytest.fixture(autouse=True)
def restore_config():
    """Deixa o módulo como estava para não contaminar os outros testes."""
    yield
    importlib.reload(config)


def reload_with_env_file(env_file: Path, monkeypatch, exported: str | None = None):
    """Recarrega `config` como se o `.env` ao lado dele fosse `env_file`."""
    monkeypatch.delenv("AGRISHIELD_API_KEY", raising=False)
    monkeypatch.delenv("AGRISHIELD_API_URL", raising=False)
    if exported is not None:
        monkeypatch.setenv("AGRISHIELD_API_KEY", exported)
    # `config` faz `from dotenv import load_dotenv` no topo, e o reload refaz esse import:
    # por isso o dublê entra no módulo `dotenv`, não em `config`.
    monkeypatch.setattr(
        dotenv, "load_dotenv", lambda _path=None, **kwargs: REAL_LOAD_DOTENV(env_file, **kwargs)
    )
    return importlib.reload(config)


def write_env(tmp_path: Path, content: str) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")
    return env_file


def test_reads_the_api_key_from_the_env_file(tmp_path, monkeypatch) -> None:
    env_file = write_env(
        tmp_path, "AGRISHIELD_API_URL=http://api.local:9000\nAGRISHIELD_API_KEY=chave-do-env\n"
    )

    reloaded = reload_with_env_file(env_file, monkeypatch)

    assert reloaded.API_KEY == "chave-do-env"
    assert reloaded.API_URL == "http://api.local:9000"


def test_exported_variable_wins_over_the_env_file(tmp_path, monkeypatch) -> None:
    """O script de demo exporta as variáveis para os dois processos: elas têm precedência."""
    env_file = write_env(tmp_path, "AGRISHIELD_API_KEY=chave-do-env\n")

    reloaded = reload_with_env_file(env_file, monkeypatch, exported="chave-exportada")

    assert reloaded.API_KEY == "chave-exportada"


def test_without_env_file_the_defaults_hold(tmp_path, monkeypatch) -> None:
    reloaded = reload_with_env_file(tmp_path / "nao-existe.env", monkeypatch)

    assert reloaded.API_KEY == ""
    assert reloaded.API_URL == "http://localhost:8000"


def test_loads_the_env_next_to_config_not_the_working_directory(monkeypatch) -> None:
    """`streamlit run front-web/app.py` a partir da raiz tem de achar o mesmo `.env`."""
    loaded: list[Path] = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda path=None, **kwargs: loaded.append(path))

    reloaded = importlib.reload(config)

    expected = Path(reloaded.__file__).resolve().parent / ".env"
    assert loaded == [expected]
    assert expected == reloaded.ENV_FILE


def test_a_dotenv_in_the_working_directory_is_ignored(tmp_path, monkeypatch) -> None:
    """Isca: um `.env` de outro projeto no diretório de onde se rodou não pode ser lido."""
    (tmp_path / ".env").write_text("AGRISHIELD_API_KEY=chave-do-cwd\n", encoding="utf-8")
    monkeypatch.delenv("AGRISHIELD_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    reloaded = importlib.reload(config)

    assert reloaded.API_KEY != "chave-do-cwd"
