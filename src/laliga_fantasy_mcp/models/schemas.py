"""Modelos Pydantic de entrada de las tools."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from laliga_fantasy_mcp.config import WEEK_NUMBER_MAX


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class BaseToolInput(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )

    league_id: Optional[str] = Field(
        default=None,
        description="ID de liga privada. Si se omite, se usa LALIGA_FANTASY_LEAGUE_ID o la primera liga.",
        max_length=64,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Formato de salida: markdown (legible) o json (estructurado).",
    )


class EmptyInput(BaseToolInput):
    pass


class RivalsInput(BaseToolInput):
    query: Optional[str] = Field(
        default=None,
        description="Nombre o ID de mánager/equipo rival. Vacío lista todos.",
        max_length=120,
    )


class PlayerQueryInput(BaseToolInput):
    player_id_or_name: str = Field(
        ...,
        description="ID numérico o nombre/apodo del jugador (ej. '162', 'Pedri').",
        min_length=1,
        max_length=120,
    )


class PlayerOffersInput(BaseToolInput):
    player_id_or_name: str = Field(
        ...,
        description="ID, playerTeamId o nombre del jugador cuyas ofertas se consultan.",
        min_length=1,
        max_length=120,
    )


class SearchPlayersInput(BaseToolInput):
    query: Optional[str] = Field(default=None, description="Texto a buscar en nombre o apodo.", max_length=120)
    position: Optional[str] = Field(
        default=None,
        description="Posición: portero, defensa, centrocampista, delantero o 1-4.",
        max_length=40,
    )
    team: Optional[str] = Field(default=None, description="Equipo real de LaLiga.", max_length=80)
    status: Optional[str] = Field(
        default=None,
        description="Filtro de estado (ok, injured, doubtful, suspended, etc.).",
        max_length=40,
    )
    min_value: Optional[int] = Field(default=None, description="Valor de mercado mínimo en euros.", ge=0)
    max_value: Optional[int] = Field(default=None, description="Valor de mercado máximo en euros.", ge=0)
    limit: Optional[int] = Field(default=25, description="Máximo de resultados.", ge=1, le=100)
    offset: Optional[int] = Field(default=0, description="Desplazamiento de paginación.", ge=0)

    @field_validator("position")
    @classmethod
    def validate_position(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        from laliga_fantasy_mcp.services.helpers import position_id

        if position_id(value) is None:
            raise ValueError(
                "position debe ser portero, defensa, centrocampista, delantero o 1-4"
            )
        return value


class LineupsInput(BaseToolInput):
    team_or_match: Optional[str] = Field(
        default=None,
        description="Slug/nombre de equipo (ej. 'real-madrid') o 'Barcelona vs Real Madrid'. Vacío = los 20.",
        max_length=160,
    )


class FixturesInput(BaseToolInput):
    week: Optional[int] = Field(
        default=None,
        description="Número de jornada (1-42). Si se omite, se usa la jornada actual/siguiente.",
        ge=1,
        le=WEEK_NUMBER_MAX,
    )


class WeekInput(BaseToolInput):
    week: Optional[int] = Field(
        default=None,
        description="Número de jornada (1-42). Si se omite, se usa la jornada actual/siguiente.",
        ge=1,
        le=WEEK_NUMBER_MAX,
    )


class LineupInput(BaseToolInput):
    team_or_manager: Optional[str] = Field(
        default=None,
        description="ID de equipo fantasy, nombre de mánager o 'me' para el propio. Por defecto el usuario.",
        max_length=120,
    )
    week: Optional[int] = Field(
        default=None,
        description="Jornada de la alineación (1-42). Vacío = jornada actual (snapshot visible de rivales, como LaLigaApp). El XI editable propio solo se ve en laliga_get_my_team.",
        ge=1,
        le=WEEK_NUMBER_MAX,
    )


class ActivityInput(BaseToolInput):
    page: Optional[int] = Field(
        default=0,
        description="Página del feed concatenado (0 = más reciente). offset = page * limit; usa el mismo limit entre páginas.",
        ge=0,
        le=200,
    )
    limit: Optional[int] = Field(default=40, description="Máximo de eventos a devolver.", ge=1, le=100)
    offset: Optional[int] = Field(
        default=None,
        description="Desplazamiento absoluto. Si se indica, tiene prioridad sobre page.",
        ge=0,
    )


class TrendsInput(BaseToolInput):
    filter: Optional[str] = Field(
        default="all",
        description="Filtro: all, rising, falling, stable.",
        max_length=20,
    )
    position: Optional[str] = Field(default=None, description="Posición a filtrar.", max_length=40)
    limit: Optional[int] = Field(default=30, description="Máximo de jugadores.", ge=1, le=100)

    @field_validator("filter")
    @classmethod
    def validate_filter(cls, value: str) -> str:
        allowed = {"all", "rising", "falling", "stable"}
        normalized = (value or "all").lower()
        if normalized not in allowed:
            raise ValueError("filter debe ser all, rising, falling o stable")
        return normalized


class OwnershipInput(BaseToolInput):
    query: Optional[str] = Field(
        default=None,
        description="Filtrar por jugador, mánager o 'unlocked'/'locked' para cláusulas.",
        max_length=120,
    )
    limit: Optional[int] = Field(default=50, description="Máximo de filas.", ge=1, le=200)


class MarketHistoryInput(BaseToolInput):
    limit: Optional[int] = Field(default=40, description="Máximo de movimientos.", ge=1, le=200)
    query: Optional[str] = Field(default=None, description="Filtro por jugador o mánager.", max_length=120)
