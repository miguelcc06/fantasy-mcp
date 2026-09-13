"""Mapa de dueños, cláusulas y blindajes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from laliga_fantasy_mcp.client.api import FantasyClient
from laliga_fantasy_mcp.config import OWNERSHIP_CONCURRENCY
from laliga_fantasy_mcp.services.helpers import (
    as_int,
    extract_array,
    manager_name,
    player_id,
    player_name,
    season_points,
    team_id_from,
    unwrap,
)


def _players_from_team(team_data: Any) -> list[dict[str, Any]]:
    payload = unwrap(team_data) if isinstance(team_data, dict) else team_data
    if isinstance(payload, dict):
        for key in ("players", "playerTeam", "playerTeams"):
            if isinstance(payload.get(key), list):
                return payload[key]
        nested = payload.get("data")
        if isinstance(nested, dict):
            return _players_from_team(nested)
        if isinstance(nested, list):
            return nested
    if isinstance(payload, list):
        return payload
    return extract_array(team_data)


async def build_ownership(client: FantasyClient, league_id: str, standings: list[Any]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(OWNERSHIP_CONCURRENCY)
    rows: list[dict[str, Any]] = []

    async def load_team(entry: dict[str, Any]) -> None:
        tid = team_id_from(entry)
        if not tid:
            return
        async with semaphore:
            try:
                team = await client.team(league_id, tid)
            except Exception:
                return
        owner = manager_name(entry)
        now = datetime.now(timezone.utc)
        for player in _players_from_team(team):
            if not isinstance(player, dict):
                continue
            pid = player_id(player)
            if not pid:
                continue
            locked_until = player.get("buyoutClauseLockedEndTime") or player.get("clauseLockEnd")
            locked = False
            if locked_until:
                try:
                    end = datetime.fromisoformat(str(locked_until).replace("Z", "+00:00"))
                    locked = end > now
                except ValueError:
                    locked = True
            rows.append(
                {
                    "playerId": pid,
                    "playerTeamId": str(player.get("playerTeamId") or player.get("id") or pid),
                    "name": player_name(player),
                    "owner": owner,
                    "teamId": tid,
                    "buyoutClause": as_int(player.get("buyoutClause") or player.get("clause")),
                    "buyoutClauseLockedEndTime": locked_until,
                    "clauseLocked": locked,
                    "isShielded": bool(player.get("isShielded") or player.get("shielded")),
                    "marketValue": as_int(
                        (player.get("playerMaster") or {}).get("marketValue")
                        or player.get("marketValue")
                    ),
                    "points": season_points(player),
                }
            )

    await asyncio.gather(*(load_team(entry) for entry in standings if isinstance(entry, dict)))
    rows.sort(key=lambda item: (-item["buyoutClause"], item["name"]))
    return rows
