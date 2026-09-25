"""Controle de acesso por chave de API (I5, ADR-013).

O enunciado pede **controle de acesso**; login com perfis não cabe no prazo. A decisão registrada
(ADR-013) é: chave no cabeçalho `X-API-Key` nas escritas e publicações e, fora do modo `dev`
explícito, nas leituras de dados operacionais; tudo o que decide fica na trilha de auditoria.

Três cuidados que valem explicação:

- **Comparação em tempo constante** (`secrets.compare_digest`), e sem `break` no laço: sair mais
  cedo na chave certa daria, pelo tempo de resposta, uma pista de quantas chaves existem.
- **Falha fechada.** Sem `AGRISHIELD_API_KEYS` configurada, nenhuma chave pode ser válida, então
  toda operação protegida é recusada. O contrário — liberar quando ninguém configurou —
  transformaria um esquecimento de ambiente em porta aberta.
- **A chave nunca vai para o log.** A tentativa recusada é registrada com a chave **mascarada**
  (`****1234`), que é o suficiente para investigar sem guardar o segredo.
"""

import logging
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"

# Mensagem única para o cliente: não distingue "ausente" de "inválida" nem de "nenhuma
# configurada", para não contar ao atacante em que pé está a configuração do servidor.
INVALID_API_KEY_MESSAGE = "Chave de API ausente ou inválida"

# Quantos caracteres do fim da chave aparecem mascarados no log.
MASK_VISIBLE_CHARS = 4


def parse_api_keys(raw: str) -> tuple[str, ...]:
    """Lê `AGRISHIELD_API_KEYS` (separada por vírgula), ignorando espaços e itens vazios."""
    return tuple(key.strip() for key in raw.split(",") if key.strip())


def mask_api_key(key: str | None) -> str:
    """Versão da chave que pode ir para o log: `****1234`.

    Chave curta demais para ter uma cauda distinguível vira só `****`, senão o "mascarado"
    entregaria quase tudo.
    """
    if not key:
        return "(ausente)"
    if len(key) <= MASK_VISIBLE_CHARS * 2:
        return "****"
    return f"****{key[-MASK_VISIBLE_CHARS:]}"


def is_valid_api_key(candidate: str | None, configured: tuple[str, ...]) -> bool:
    """Compara em tempo constante contra todas as chaves, **sem sair mais cedo** na que casar."""
    if not candidate or not configured:
        return False

    provided = candidate.encode("utf-8")
    matched = False
    for key in configured:
        matched |= secrets.compare_digest(provided, key.encode("utf-8"))
    return matched


def require_api_key(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> str:
    """Dependência dos endpoints protegidos. Devolve a chave **mascarada**, para a auditoria.

    Levanta 401 quando a chave falta, não confere ou quando o servidor não tem nenhuma
    configurada — e registra a tentativa sem o segredo.
    """
    configured = parse_api_keys(settings.api_keys)
    if not configured:
        logger.error(
            "AGRISHIELD_API_KEYS não está configurada: toda operação protegida será recusada. "
            "Defina a variável de ambiente."
        )

    if not is_valid_api_key(x_api_key, configured):
        logger.warning(
            "Acesso negado em %s %s: chave de API %s.",
            request.method,
            request.url.path,
            mask_api_key(x_api_key),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_API_KEY_MESSAGE,
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )

    return mask_api_key(x_api_key)


def require_read_access(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    """Abre leituras apenas no modo `dev` explícito; os demais ambientes exigem chave."""
    if settings.environment == "dev":
        return
    require_api_key(request, settings, x_api_key)


ApiKeyDep = Annotated[str, Depends(require_api_key)]
