"""Resolución de liga, usuario y jornada."""

from __future__ import annotations

import os
from typing import Any

from laliga_fantasy_mcp.client.api import FantasyAPIError, FantasyClient
from laliga_fantasy_mcp.services.helpers import as_int, extract_array, fuzzy_match, manager_id_from, manager_name, player_id, player_name, team_id_from, unwrap


class LeagueContext:
    def __init__(self, client: FantasyClient) -> None:
        self.client = client
        self._user: dict[str, Any] | None = None
        self._leagues: list[Any] | None = None
        self._players: list[Any] | None = None
        self._teams_master: dict[str, dict[str, Any]] | None = None

    async def user(self) -> dict[str, Any]:
        if self._user is None:
            payload = await self.client.current_user()
            self._user = payload if isinstance(payload, dict) else {}
        return self._user

    def user_id(self, user: dict[str, Any]) -> str | None:
        raw = user.get("id") or user.get("userId") or user.get("managerId")
        return str(raw) if raw is not None else None

    async def leagues(self) -> list[Any]:
        if self._leagues is None:
            self._leagues = await self.client.leagues()
        return self._leagues

    async def league_id(self, explicit: str | None) -> str:
        if explicit:
            return explicit
        env_id = os.getenv("LALIGA_FANTASY_LEAGUE_ID")
        if env_id:
            return env_id
        leagues = await self.leagues()
        if not leagues:
            raise RuntimeError(
                "No se encontró ninguna liga. Pasa league_id o define LALIGA_FANTASY_LEAGUE_ID."
            )
        first = leagues[0]
        if isinstance(first, dict):
            raw = first.get("id") or first.get("leagueId") or (first.get("league") or {}).get("id")
            if raw is not None:
                return str(raw)
        raise RuntimeError("La respuesta de ligas no incluye un ID reconocible.")

    async def standings(self, league_id: str) -> list[Any]:
        return await self.client.standing(league_id)

    async def my_team_entry(self, league_id: str) -> dict[str, Any]:
        user = await self.user()
        uid = self.user_id(user)
        standings = await self.standings(league_id)
        for entry in standings:
            if not isinstance(entry, dict):
                continue
            team = entry.get("team") if isinstance(entry.get("team"), dict) else {}
            manager = team.get("manager") if isinstance(team.get("manager"), dict) else {}
            candidates = [
                entry.get("userId"),
                team.get("userId"),
                manager.get("id"),
                manager.get("userId"),
            ]
            if uid and any(str(item) == uid for item in candidates if item is not None):
                return entry
        if standings and isinstance(standings[0], dict):
            # Fallback: some payloads only match by manager name
            display = user.get("managerName") or user.get("displayName") or user.get("username")
            if display:
                for entry in standings:
                    if isinstance(entry, dict) and fuzzy_match(str(display), manager_name(entry)):
                        return entry
        raise RuntimeError(
            "No se localizó tu equipo en la clasificación. Comprueba league_id o el usuario de la sesión."
        )

    async def my_team_id(self, league_id: str) -> str:
        entry = await self.my_team_entry(league_id)
        tid = team_id_from(entry)
        if not tid:
            raise RuntimeError("La clasificación no incluye el ID de tu equipo.")
        return tid

    async def find_team(self, league_id: str, query: str) -> dict[str, Any]:
        standings = await self.standings(league_id)
        for entry in standings:
            if not isinstance(entry, dict):
                continue
            tid = team_id_from(entry) or ""
            if query == tid or fuzzy_match(query, manager_name(entry), tid, str(entry.get("name") or "")):
                return entry
        raise RuntimeError(f"No hay un rival que coincida con '{query}'.")

    async def current_week_number(self) -> int:
        payload = await self.client.current_week()
        if isinstance(payload, dict):
            week = payload.get("weekNumber") or payload.get("week") or payload.get("id")
            if week is not None:
                return as_int(week)
            inner = unwrap(payload)
            if isinstance(inner, dict):
                week = inner.get("weekNumber") or inner.get("week") or inner.get("id")
                if week is not None:
                    return as_int(week)
        if isinstance(payload, (int, float, str)):
            return as_int(payload)
        raise RuntimeError("No se pudo determinar la jornada actual.")

    async def players(self) -> list[Any]:
        if self._players is None:
            self._players = await self.client.players()
        return self._players

    async def teams_master(self) -> dict[str, dict[str, Any]]:
        if self._teams_master is None:
            mapping: dict[str, dict[str, Any]] = {}
            for team in await self.client.teams_master():
                if isinstance(team, dict) and team.get("id") is not None:
                    mapping[str(team["id"])] = team
            self._teams_master = mapping
        return self._teams_master

    async def manager_names(self, league_id: str) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for entry in await self.standings(league_id):
            if not isinstance(entry, dict):
                continue
            name = manager_name(entry)
            if not name:
                continue
            mid = manager_id_from(entry)
            if mid:
                mapping[mid] = name
        return mapping

    async def player_names(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for player in await self.players():
            if not isinstance(player, dict):
                continue
            pid = player_id(player)
            name = player_name(player)
            if pid and name:
                mapping[pid] = name
        return mapping

    async def enrich_calendar(self, calendar: Any) -> list[dict[str, Any]]:
        matches = calendar if isinstance(calendar, list) else extract_array(calendar)
        if isinstance(calendar, dict) and not matches:
            matches = extract_array(calendar.get("matches") or calendar.get("matchs") or calendar)
        master = await self.teams_master()
        out: list[dict[str, Any]] = []
        for item in matches:
            if not isinstance(item, dict):
                continue
            local_id = str(item.get("localId") or (item.get("local") or {}).get("id") or "")
            visitor_id = str(item.get("visitorId") or (item.get("visitor") or {}).get("id") or "")
            local = item.get("local") if isinstance(item.get("local"), dict) else master.get(local_id, {})
            visitor = item.get("visitor") if isinstance(item.get("visitor"), dict) else master.get(visitor_id, {})
            out.append(
                {
                    "id": item.get("id"),
                    "weekNumber": item.get("weekNumber") or item.get("week"),
                    "date": item.get("date") or item.get("matchDate") or item.get("schedule"),
                    "status": item.get("status"),
                    "local": local.get("name") if isinstance(local, dict) else local,
                    "localId": local_id,
                    "visitor": visitor.get("name") if isinstance(visitor, dict) else visitor,
                    "visitorId": visitor_id,
                    "localScore": item.get("localScore") or item.get("localGoals"),
                    "visitorScore": item.get("visitorScore") or item.get("visitorGoals"),
                }
            )
        return out
