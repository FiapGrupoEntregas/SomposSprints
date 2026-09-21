"""Chaves de sessão compartilhadas entre as páginas.

**Por que chave própria e nunca `key=` de widget:** o Streamlit limpa o estado com escopo de
página **na própria troca de página** — em `SessionState._remove_stale_widgets` há um passo
específico de *page switch* ("a page-scoped value must not outlive a page switch"). Não importa
se a página seguinte desenha o mesmo widget: o valor guardado na chave do widget não sobrevive
à navegação. Guardar a escolha numa chave própria do `st.session_state`, e só usar `value=` no
widget, faz a escolha atravessar as páginas (padrão fixado na W1 e cobrado na W3/W4).

Receita para um estado compartilhado novo:

```python
valor = st.toggle("Rótulo", value=bool(st.session_state.get(MINHA_CHAVE, False)))
st.session_state[MINHA_CHAVE] = valor
```

O dia e o cenário viajam do mapa de risco (W3) para o card do limite (W4): é assim que a demo
escolhe o dia chuvoso e envia o limite **daquele** dia, com ou sem cenário, para o equipamento.
"""

# Data (AAAA-MM-DD) do dia escolhido na previsão de risco e usada no limite do equipamento.
SELECTED_DATE_KEY = "selected_day_date"

# Cenário simulado ligado pelo usuário (`heavy_rain` ou nada).
SCENARIO_KEY = "risk_scenario_heavy_rain"
