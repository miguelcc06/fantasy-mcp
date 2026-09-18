"""Cliente HTTP de solo lectura para fantasy-api.llt-services.com."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from laliga_fantasy_mcp.auth.token_manager import TokenManager
from laliga_fantasy_mcp.config import (
    API_BASE_URL,
    CMP,
    HTTP_TIMEOUT,
    STATS_BASE_URL,
    USER_AGENT,
)
from laliga_fantasy_mcp.services.helpers import extract_array, unwrap

logger = logging.getLogger("laliga_fantasy_mcp.api")


class FantasyAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FantasyClient:
    def __init__(self, tokens: TokenManager) -> None:
        self.tokens = tokens
        self._http = httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            headers={
                "User-Agent": USER_AGENT,
                "x-lang": "es",
                "x-app": "2",
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, *, params: dict[str, Any] | None = None, stats: bool = False) -> Any:
        await self.tokens.ensure_fresh(self._http)
        url = path if path.startswith("http") else (
            f"{STATS_BASE_URL}{path}" if stats else f"{API_BASE_URL}{path}"
        )
        query = {"x-lang": "es", **(params or {})}
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._http.get(
                    url,
                    params=query,
                    headers={"Authorization": f"Bearer {self.tokens.bearer()}"},
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                continue
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            if response.status_code == 401 and attempt == 0:
                await self.tokens.refresh(self._http)
                continue
            if response.status_code in {429, 500, 502, 503} and attempt < 2:
                continue
            if response.status_code == 204 or not response.content:
                return None
            if response.status_code == 404:
                raise FantasyAPIError("Recurso no encontrado (404).", 404)
            if response.status_code >= 400:
                raise FantasyAPIError(
                    _describe_status(response.status_code),
                    response.status_code,
                )
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                return response.json()
            return response.text
        raise FantasyAPIError(f"Error de red: {last_error or 'timeout'}")

    async def put(self, path: str, *, json_data: Any = None) -> Any:
        await self.tokens.ensure_fresh(self._http)
        url = path if path.startswith("http") else f"{API_BASE_URL}{path}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._http.put(
                    url,
                    params={"x-lang": "es"},
                    json=json_data,
                    headers={
                        "Authorization": f"Bearer {self.tokens.bearer()}",
                        "Content-Type": "application/json",
                        "x-lang": "es",
                    },
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                continue
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            if response.status_code == 401 and attempt == 0:
                await self.tokens.refresh(self._http)
                continue
            # No reintentar 500 en escrituras: suele ser payload inválido y
            # un reintento podría duplicar un cambio si el primero sí persistió.
            if response.status_code in {429, 502, 503} and attempt < 2:
                continue
            if response.status_code >= 400:
                raise FantasyAPIError(
                    _describe_status(response.status_code, _response_detail(response)),
                    response.status_code,
                )
            if response.status_code == 204 or not response.content:
                return None
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                return response.json()
            return response.text
        raise FantasyAPIError(f"Error de red: {last_error or 'timeout'}")

    async def set_lineup(self, team_id: str, payload: dict[str, Any]) -> Any:
        """Guarda el XI editable: PUT /v1/competition/{id}/teams/{team_id}/lineup.

        El body de escritura NO es el JSON de GET. La app envía IDs planos y
        `tactical_formation` en snake_case (sin wrapper `formation`).
        """
        extras = ("coach", "captain", "bench")
        try:
            return unwrap(await self.put(f"{CMP}/teams/{team_id}/lineup", json_data=payload))
        except FantasyAPIError as exc:
            base = {key: value for key, value in payload.items() if key not in extras}
            if base != payload and exc.status_code and exc.status_code >= 400:
                return unwrap(await self.put(f"{CMP}/teams/{team_id}/lineup", json_data=base))
            raise

    async def current_user(self) -> dict[str, Any]:
        return unwrap(await self.get("/v4/user/me")) or {}

    async def leagues(self) -> list[Any]:
        return extract_array(await self.get(f"{CMP}/leagues"))

    async def standing(self, league_id: str) -> list[Any]:
        return extract_array(await self.get(f"{CMP}/leagues/{league_id}/standing"))

    async def standing_week(self, league_id: str, week: int) -> list[Any]:
        return extract_array(await self.get(f"{CMP}/leagues/{league_id}/standing/{week}"))

    async def activity(self, league_id: str, page: int = 0) -> list[Any]:
        return extract_array(await self.get(f"{CMP}/leagues/{league_id}/activity/{page}"))

    async def team(self, league_id: str, team_id: str) -> dict[str, Any]:
        return unwrap(await self.get(f"{CMP}/leagues/{league_id}/teams/{team_id}")) or {}

    async def team_money(self, team_id: str) -> Any:
        return unwrap(await self.get(f"{CMP}/teams/{team_id}/money"))

    async def current_lineup(self, team_id: str) -> Any:
        """XI editable. Solo el dueño; en rivales la API responde 403."""
        return unwrap(await self.get(f"{CMP}/teams/{team_id}/lineup"))

    async def lineup(self, team_id: str, week: int | None = None) -> Any:
        """Snapshot de jornada (el que usa LaLigaApp para ver rivales).

        Sin week se pide el XI editable (solo funciona en el equipo propio).
        """
        if week is None:
            return await self.current_lineup(team_id)
        return unwrap(await self.get(f"{CMP}/teams/{team_id}/lineup/week/{week}"))

    async def market(self, league_id: str) -> list[Any]:
        return extract_array(await self.get(f"{CMP}/league/{league_id}/market"))

    async def market_history(self, league_id: str) -> list[Any]:
        return extract_array(await self.get(f"{CMP}/league/{league_id}/market/history"))

    async def player_offer(self, league_id: str, player_team_id: str) -> Any:
        return unwrap(await self.get(f"{CMP}/league/{league_id}/playerTeam/{player_team_id}/offer"))

    async def players(self) -> list[Any]:
        return extract_array(await self.get(f"{CMP}/players"))

    async def player_details(self, player_id: str, league_id: str) -> dict[str, Any]:
        return unwrap(await self.get(f"{CMP}/player/{player_id}/league/{league_id}")) or {}

    async def player_market_value(self, player_id: str) -> Any:
        return unwrap(await self.get(f"{CMP}/player/{player_id}/market-value"))

    async def current_week(self) -> Any:
        return unwrap(await self.get(f"{CMP}/week/current"))

    async def calendar(self, week: int) -> Any:
        return unwrap(await self.get(f"{CMP}/calendar", params={"weekNumber": week}))

    async def teams_master(self) -> list[Any]:
        return extract_array(await self.get("/v3/teams-master"))

    async def formations(self, option: str = "free") -> Any:
        return unwrap(await self.get("/v4/teams/lineup/formations", params={"option": option}))

    async def week_stats(self, week: int) -> Any:
        return unwrap(
            await self.get(
                f"/stats/v1/competition/1/stats/week/{week}",
                stats=True,
            )
        )


def _response_detail(response: httpx.Response) -> str | None:
    raw = (response.text or "").strip()
    if not raw:
        return None
    try:
        data = response.json()
    except Exception:
        return raw[:280]
    if isinstance(data, dict):
        for key in ("message", "error", "detail", "title"):
            value = data.get(key)
            if value:
                return str(value)[:280]
        return str(data)[:280]
    return str(data)[:280]


def _describe_status(status: int, detail: str | None = None) -> str:
    mapping = {
        401: "Error: autenticación inválida o caducada. Renueva LALIGA_FANTASY_REFRESH_TOKEN en el .env.",
        403: "Error: acceso denegado a este recurso de la liga.",
        404: "Error: recurso no encontrado. Comprueba el ID.",
        429: "Error: demasiadas peticiones. Espera unos segundos y reintenta.",
        500: "Error: fallo del servidor de LaLiga Fantasy.",
        502: "Error: pasarela de LaLiga Fantasy no disponible.",
    }
    msg = mapping.get(status, f"Error: la API respondió {status}.")
    cleaned = " ".join((detail or "").split())
    if cleaned and cleaned.lower() not in {"internal server error", str(status)}:
        return f"{msg} Detalle: {cleaned[:280]}"
    return msg
