"""Schemas das recomendações e das janelas seguras (W6).

As janelas vêm de `regras-de-risco §7`
([document/regras-de-risco.md](../../../document/regras-de-risco.md)) e as frases são
**templates com números**, nunca texto gerado por IA: o operador precisa de uma orientação curta
que sempre diga a mesma coisa para a mesma situação.
"""

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TimeWindow(BaseModel):
    """Um intervalo contínuo de horas seguras, no fuso da fazenda."""

    model_config = ConfigDict(extra="forbid")

    start: dt.time = Field(description="Início da janela (hora cheia).")
    end: dt.time = Field(description="Fim da janela (hora cheia, exclusivo).")

    @model_validator(mode="after")
    def check_order(self) -> "TimeWindow":
        """Uma janela que termina antes de começar seria um erro de cálculo, não um dado."""
        if self.end <= self.start:
            raise ValueError(f"Janela inválida: {self.start} não é antes de {self.end}.")
        return self

    @property
    def hours(self) -> float:
        """Duração em horas, para o filtro de duração mínima."""
        start = self.start.hour + self.start.minute / 60
        end = self.end.hour + self.end.minute / 60
        return end - start


class DayRecommendation(BaseModel):
    """O que fazer num dia: as janelas seguras e as frases (W6)."""

    model_config = ConfigDict(extra="forbid")

    date: dt.date = Field(description="Dia a que a recomendação se refere.")
    windows: list[TimeWindow] = Field(
        default_factory=list,
        description="Janelas seguras de operação, em ordem cronológica (§7).",
    )
    messages: list[str] = Field(
        default_factory=list,
        description="Orientações em português, geradas por template, da mais grave à menos.",
    )
