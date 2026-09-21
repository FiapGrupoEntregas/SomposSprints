# Trilha do time (T): o que não é código

Metade da nota vem daqui. **Casos reais, números e um pitch bem contado** são o que convence a banca.
Cada entrega vira uma issue do tipo *Task* com a label `pesquisa`.

| ID | Entrega | Prazo | Resultado esperado |
|---|---|---|---|
| T1 | Pesquisa de limiares e normas | 17/09 | Tabela com fonte para cada limiar de `document/regras-de-risco.md` |
| T2 | Casos reais para o replay + validação das fazendas | 18/09 | ≥ 5 casos no formato do `replay_cases.json` + coordenadas conferidas |
| T3 | Estatísticas e dores do mercado | 19/09 | 1 página com números e fontes + 1–2 entrevistas |
| T4 | Pitch e modelo de negócio | 22/09 | Deck com ~10 slides |
| T5 | Vídeo de backup e ensaios | 24–26/09 | Vídeo de ≤ 4 min + 3 ensaios cronometrados |
| T6 | Validar o circuito no Wokwi | 16/09 | Simulação rodando + tabela de ângulos conferida |
| QA | Testar os PRs | contínuo | Aprovar ou comentar seguindo o "Como testar" |

---

## T1 — Pesquisa de limiares e normas

- Para **cada limiar** de [regras-de-risco.md](../document/regras-de-risco.md), encontre uma fonte que o confirme ou o corrija:
  - inclinação lateral máxima segura para tratores (manuais de fabricantes, NR-31, Fundacentro, ISO 5700/ROPS);
  - efeito do solo molhado na estabilidade e na tração;
  - "regra dos 30" para incêndios (Corpo de Bombeiros, Embrapa);
  - rajadas de vento e pulverização/máquinas altas;
  - raios em áreas abertas e topos de morro.
- Entrega: uma tabela `limiar | valor atual | valor sugerido | fonte (link)` enviada ao dev ou num PR em `document/regras-de-risco.md`.

## T2 — Casos reais para o replay e validação das fazendas

- Busque em notícias (G1, portais regionais, Canal Rural) **capotamentos e acidentes com máquinas agrícolas** que tenham data e município.
- Para cada caso, preencha: título, data, município/UF, local (lat/lon aproximado pelo Google Maps), precisão (`exato` / `aproximado` / `municipio`), máquina, resumo de 1–2 frases e link.
- Prefira casos **em relevo acidentado e perto de dias de chuva**, sem esconder os que não se encaixam.
- Confira no Google Maps (satélite) se as bboxes das 3 fazendas de demonstração (W1) caem em áreas agrícolas reais.

## T3 — Estatísticas e dores do mercado

- Números de acidentes com tratores e máquinas agrícolas no Brasil (mortes, afastamentos): Ministério do Trabalho, Fundacentro, DataSUS.
- Relevância do seguro de máquinas agrícolas e dos sinistros por tombamento, alagamento e incêndio (SUSEP, notícias do setor, relatórios da própria Sompo).
- **Entrevistas** (1–2 pessoas: produtor, operador ou corretor), com 5 perguntas:
  1. Já teve ou viu acidente com máquina em terreno inclinado? O que aconteceu?
  2. A chuva muda o jeito de operar na encosta? Como decidem se dá para entrar?
  3. Um aviso no trator com o limite do dia ajudaria? O que atrapalharia?
  4. Aceitaria um dispositivo desses em troca de desconto no seguro?
  5. Quanto custa, em dinheiro e em tempo, uma máquina parada por acidente?
- **Nunca invente números.** Todo número no pitch precisa ter fonte.

## T4 — Pitch e modelo de negócio

Estrutura sugerida (~10 slides):
1. O problema: acidentes com máquinas + seguro reativo
2. O insight: **"um terreno seguro hoje pode capotar um trator amanhã"**
3. O que ninguém olha: o **relevo** (o gap que a Sompo apontou)
4. A solução: mapa de risco relevo × clima + limite dinâmico na máquina
5. **Demo** (ao vivo)
6. Validação: replay de casos reais + pesquisa (T1, T3)
7. Valor para a Sompo: prevenção (menos sinistros), subscrição sem questionário e dados para sinistro e renovação
8. Modelo de negócio: por exemplo, benefício incluso para segurados, desconto por adesão e dispositivo em comodato
9. Roadmap: calibração com a base de sinistros → ML → Passaporte Digital → app para o operador
10. Time e próximos passos

## T5 — Vídeo de backup e ensaios

- Grave o fluxo completo do [document/demo.md](../document/demo.md) em 24/09 (1080p, ≤ 4 min, com narração).
- Faça 3 ensaios cronometrados, com um responsável por cada parte da fala.

## T6 — Validar o circuito no Wokwi

- Siga a "Opção 2" do [iot/README.md](../iot/README.md) (Wokwi no navegador).
- Confirme que o Serial mostra `[wifi] conectado` e `[mqtt] conectado`.
- Para cada linha da tabela de ângulos, ajuste o MPU6050 e anote o roll lido. Envie print ou GIF.
