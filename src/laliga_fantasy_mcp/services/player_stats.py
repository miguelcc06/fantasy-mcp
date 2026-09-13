"""Resolución de jugadores y resumen de fichas."""

from __future__ import annotations

import asyncio
from typing import Any

from laliga_fantasy_mcp.client.api import FantasyAPIError, FantasyClient
from laliga_fantasy_mcp.config import SEARCH_ENRICH_CONCURRENCY
from laliga_fantasy_mcp.services.helpers import (
    as_int,
    club_name,
    fold,
    fuzzy_match,
    nested_player,
    pick_player_status,
    player_id,
    player_name,
    position_id,
    season_points,
)


def _is_out_of_league(player: dict[str, Any]) -> bool:
    return fold(pick_player_status(player)) in {"out_of_league", "outofleague"}


def find_players(catalog: list[Any], query: str, limit: int = 8) -> list[dict[str, Any]]:
    needle = fold(query)
    if not needle:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for player in catalog:
        if not isinstance(player, dict):
            continue
        name = player_name(player)
        pid = player_id(player) or ""
        if needle == fold(pid):
            scored.append((0, player))
            continue
        nick = fold(player.get("nickname") or name)
        full = fold(player.get("name") or name)
        score = 99
        if not nick and not full:
            continue
        if needle == nick or needle == full:
            score = 1
        elif len(needle) >= 2 and (nick.startswith(needle) or full.startswith(needle)):
            score = 2
        elif len(needle) >= 3 and (needle in nick or needle in full):
            score = 3
        elif fuzzy_match(query, name, player.get("nickname"), player.get("name")):
            score = 4
        if score >= 99:
            continue
        if _is_out_of_league(player) and score > 1:
            continue
        scored.append((score, player))
    scored.sort(key=lambda item: (item[0], -(season_points(item[1]) or 0)))
    return [item[1] for item in scored[:limit]]


async def enrich_player(client: FantasyClient, player: dict[str, Any], league_id: str) -> dict[str, Any]:
    pid = player_id(player)
    details: dict[str, Any] = dict(player)
    if pid:
        try:
            extra = await client.player_details(pid, league_id)
            if isinstance(extra, dict):
                master = extra.get("playerMaster")
                if isinstance(master, dict):
                    current = details.get("playerMaster") if isinstance(details.get("playerMaster"), dict) else {}
                    details["playerMaster"] = {**current, **master}
                    details["playerStats"] = master.get("playerStats") or extra.get("playerStats")
                details.update({key: value for key, value in extra.items() if key != "playerMaster"})
        except FantasyAPIError:
            pass
        try:
            history = await client.player_market_value(pid)
            details["marketValueHistory"] = history
        except FantasyAPIError:
            details["marketValueHistory"] = []
    return details


async def enrich_player_page(
    client: FantasyClient,
    players: list[dict[str, Any]],
    league_id: str,
    concurrency: int = SEARCH_ENRICH_CONCURRENCY,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(player: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await enrich_player(client, player, league_id)

    if not players:
        return []
    return list(await asyncio.gather(*(one(player) for player in players)))


def summarize_player(
    player: dict[str, Any],
    teams_master: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    master = nested_player(player)
    pos = position_id(master.get("positionId") or master.get("position") or player.get("positionId"))
    pts = season_points(player)
    return {
        "id": player_id(player),
        "name": player_name(player),
        "nickname": master.get("nickname") or player.get("nickname"),
        "team": club_name(player, teams_master),
        "teamId": str(master.get("teamId") or player.get("teamId") or "") or None,
        "positionId": pos,
        "marketValue": as_int(master.get("marketValue") or player.get("marketValue")),
        "points": pts,
        "seasonPoints": pts,
        "averagePoints": master.get("averagePoints") or player.get("averagePoints"),
        "status": master.get("playerStatus") or player.get("playerStatus") or player.get("status") or pick_player_status(player),
        "weekPoints": player.get("playerStatusWeek") or player.get("weekPoints") or master.get("weekPoints"),
        "stats": player.get("playerStats") or player.get("stats") or master.get("stats") or player.get("playedWeeks"),
        "marketValueHistory": player.get("marketValueHistory"),
    }
