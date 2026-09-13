"""Utilidades de normalización de respuestas de la API."""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from typing import Any

from laliga_fantasy_mcp.config import (
    CURRENT_LALIGA_SLUGS,
    LALIGA_TEAMS,
    MARKET_ACTIVITY_TYPES,
    OPERATION_FROM_ACTIVITY,
)

FUZZY_RATIO_MIN = 0.86
FUZZY_RATIO_MIN_LEN = 4
TEAM_SUBSTRING_MIN_LEN = 4


def extract_array(payload: Any) -> list[Any]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("elements", "leagues", "data", "players", "items", "results", "playerTeams"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = extract_array(value)
            if nested:
                return nested
    inner = payload.get("data")
    if inner is not None and inner is not payload:
        return extract_array(inner)
    return []


def unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload and len(payload) <= 3:
        return payload["data"]
    return payload


def as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_money(value: Any) -> int:
    if isinstance(value, dict):
        return as_money(
            value.get("teamMoney")
            or value.get("amount")
            or value.get("money")
            or value.get("value")
            or 0
        )
    return as_int(value, 0)


def fold(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return " ".join(stripped.lower().split())


def fold_team(text: str | None) -> str:
    """Normaliza slugs y nombres de club (guiones y puntos = espacio)."""
    if not text:
        return ""
    return fold(text.replace("-", " ").replace(".", " "))


def resolve_team_slug(query: str) -> str | None:
    """Resuelve un club a su slug FF. Exacto primero; no 'oviedo' dentro de 'real-oviedo'."""
    needle = fold_team(query)
    if not needle:
        return None
    compact = needle.replace(" ", "")
    for slug, info in LALIGA_TEAMS.items():
        names = (slug, info["name"], info["fullName"])
        candidates = {fold_team(item) for item in names}
        compact_candidates = {fold_team(item).replace(" ", "") for item in names}
        if needle in candidates or compact in compact_candidates:
            return slug
    hits: list[str] = []
    for slug, info in LALIGA_TEAMS.items():
        names = (slug, info["name"], info["fullName"])
        tokens: set[str] = set()
        compact_hay = ""
        for item in names:
            folded = fold_team(item)
            compact_hay += folded.replace(" ", "")
            tokens.update(tok for tok in folded.split() if len(tok) >= TEAM_SUBSTRING_MIN_LEN)
        if needle in tokens or (len(compact) >= TEAM_SUBSTRING_MIN_LEN and compact in compact_hay):
            hits.append(slug)
    if not hits:
        return None
    hyphen = needle.replace(" ", "-")
    if hyphen in hits:
        return hyphen
    real_slug = f"real-{hyphen}"
    if real_slug in hits:
        return real_slug
    return hits[0]


def is_current_laliga_slug(slug: str | None) -> bool:
    return bool(slug) and slug in CURRENT_LALIGA_SLUGS


def _norm_operation(value: Any) -> str:
    text = fold(str(value or ""))
    if text in {"purchase", "compro", "ficho", "buy"}:
        return "purchase"
    if text in {"sale", "vendio", "sell"}:
        return "sale"
    if text in {"clause", "clausulo"}:
        return "clause"
    return text


def history_dedup_key(row: dict[str, Any]) -> tuple[str, int, str]:
    pid = fold(str(row.get("playerId") or ""))
    name = fold(str(row.get("player") or ""))
    amount = as_int(row.get("amount"))
    return (pid or name, amount, _norm_operation(row.get("operation")))


def history_from_activity(
    events: list[dict[str, Any]],
    managers: dict[str, str],
    players: dict[str, str],
) -> list[dict[str, Any]]:
    """Movimientos de mercado de toda la liga a partir del feed de actividad."""
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        atype = as_int(event.get("activityTypeId"))
        if atype not in MARKET_ACTIVITY_TYPES:
            continue
        pid = str(event.get("playerMasterId") or "") or (player_id(event) or "")
        actor_id = str(event.get("user1Id") or "")
        other_id = str(event.get("user2Id") or "")
        rows.append(
            {
                "player": players.get(pid) or player_name(event) or event.get("playerName"),
                "playerId": pid or None,
                "operation": OPERATION_FROM_ACTIVITY.get(atype, str(atype)),
                "amount": as_int(event.get("amount") or event.get("money")),
                "date": event.get("createdAt") or event.get("timestamp") or event.get("date"),
                "manager": managers.get(actor_id) or manager_name(event) or None,
                "otherManager": managers.get(other_id) if other_id else None,
                "source": "activity",
            }
        )
    return rows


def merge_market_history(
    history_items: list[Any],
    events: list[dict[str, Any]],
    managers: dict[str, str],
    players: dict[str, str],
) -> list[dict[str, Any]]:
    """Une /market/history (a menudo solo el usuario) con compras/ventas del feed."""
    from_activity = history_from_activity(events, managers, players)
    from_api: list[dict[str, Any]] = []
    for item in history_items:
        if not isinstance(item, dict):
            continue
        row = enrich_history_item(item, events, managers, players)
        row.setdefault("source", "history")
        from_api.append(row)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for row in from_activity + from_api:
        if not row.get("player") and not row.get("playerId"):
            continue
        key = history_dedup_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def position_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = fold(str(value))
    mapping = {
        "1": 1,
        "portero": 1,
        "gk": 1,
        "por": 1,
        "2": 2,
        "defensa": 2,
        "def": 2,
        "3": 3,
        "centrocampista": 3,
        "mediocampista": 3,
        "medio": 3,
        "mid": 3,
        "4": 4,
        "delantero": 4,
        "del": 4,
        "forward": 4,
        "att": 4,
    }
    return mapping.get(text)


def nested_player(player: dict[str, Any] | None) -> dict[str, Any]:
    if not player:
        return {}
    for key in ("playerMaster", "player"):
        value = player.get(key)
        if isinstance(value, dict):
            return value
    return player


def player_name(player: dict[str, Any] | None) -> str:
    if not player:
        return ""
    master = nested_player(player)
    return (
        master.get("nickname")
        or master.get("name")
        or player.get("nickname")
        or player.get("name")
        or player.get("playerName")
        or ""
    )


def player_id(player: dict[str, Any] | None) -> str | None:
    if not player:
        return None
    master = nested_player(player)
    raw = (
        master.get("id")
        or player.get("playerMasterId")
        or player.get("playerId")
        or (None if player.get("activityTypeId") is not None else player.get("id"))
    )
    return str(raw) if raw is not None else None


def club_name(player: dict[str, Any] | None, teams_master: dict[str, dict[str, Any]] | None = None) -> str:
    if not player:
        return ""
    master = nested_player(player)
    team = master.get("team") if isinstance(master.get("team"), dict) else player.get("team")
    if isinstance(team, dict) and (team.get("name") or team.get("shortName")):
        return team.get("name") or team.get("shortName") or ""
    if isinstance(team, str) and team:
        return team
    tid = master.get("teamId") if master.get("teamId") is not None else player.get("teamId")
    if tid is not None and teams_master:
        info = teams_master.get(str(tid)) or {}
        return info.get("name") or info.get("shortName") or ""
    return ""


def manager_id_from(item: dict[str, Any] | None) -> str | None:
    if not item:
        return None
    team = item.get("team") if isinstance(item.get("team"), dict) else {}
    manager = team.get("manager") if isinstance(team.get("manager"), dict) else item.get("manager")
    if isinstance(manager, dict) and manager.get("id") is not None:
        return str(manager["id"])
    keys = ("user1Id", "userId", "managerId", "ownerId")
    if item.get("activityTypeId") is None:
        keys = ("userId", "managerId", "ownerId")
    for key in keys:
        if item.get(key) is not None:
            return str(item[key])
    if team.get("managerId") is not None:
        return str(team["managerId"])
    return None


def manager_name(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    team = entry.get("team") if isinstance(entry.get("team"), dict) else {}
    manager = team.get("manager") if isinstance(team.get("manager"), dict) else entry.get("manager")
    if isinstance(manager, dict):
        return manager.get("managerName") or manager.get("name") or ""
    return (
        entry.get("managerName")
        or team.get("name")
        or entry.get("name")
        or ""
    )


def team_id_from(entry: dict[str, Any] | None) -> str | None:
    if not entry:
        return None
    team = entry.get("team") if isinstance(entry.get("team"), dict) else {}
    raw = team.get("id") or entry.get("teamId")
    if raw is not None:
        return str(raw)
    if entry.get("activityTypeId") is not None or entry.get("playerMasterId") is not None:
        return None
    raw = entry.get("id")
    return str(raw) if raw is not None else None


def fuzzy_match(query: str, *candidates: str | None) -> bool:
    """True si query coincide con algún candidato (igualdad, substring o ratio alto).

    No usa contención inversa (candidato dentro de la query): nombres cortos
    como «Noé» no deben encajar en basura tipo «xyznoexiste123».
    """
    needle = fold(query)
    if not needle:
        return True
    for candidate in candidates:
        hay = fold(candidate)
        if not hay:
            continue
        if needle == hay or needle in hay:
            return True
        if len(needle) >= FUZZY_RATIO_MIN_LEN and SequenceMatcher(None, needle, hay).ratio() >= FUZZY_RATIO_MIN:
            return True
    return False


def _scalar_points(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, list):
        return None
    return as_int(value)


def season_points(player: dict[str, Any] | None) -> int | None:
    """Puntos de temporada. None si el payload no trae el dato (no inventar 0)."""
    if not player:
        return None
    master = nested_player(player)
    pts = _scalar_points(master.get("points"))
    if pts is not None:
        return pts
    stats = (
        player.get("playerStats")
        or player.get("stats")
        or master.get("playerStats")
        or master.get("stats")
        or player.get("playedWeeks")
        or []
    )
    if isinstance(stats, list) and stats:
        total = 0
        found = False
        for row in stats:
            if not isinstance(row, dict):
                continue
            raw = row.get("totalPoints")
            if raw is None:
                raw = row.get("points")
            if raw is None:
                continue
            found = True
            total += as_int(raw)
        if found:
            return total
    if master is player:
        return _scalar_points(player.get("points"))
    return None


def lineup_week_points(player: dict[str, Any] | None) -> int | None:
    """Puntos de jornada de un ítem de alineación (no temporada)."""
    if not player:
        return None
    for key in ("weekPoints", "pointsWeek", "livePoints"):
        pts = _scalar_points(player.get(key))
        if pts is not None:
            return pts
    master = nested_player(player)
    if master is not player:
        pts = _scalar_points(master.get("weekPoints"))
        if pts is not None:
            return pts
        return _scalar_points(player.get("points"))
    return None


def _nested_manager_name(blob: Any) -> str:
    if isinstance(blob, str) and blob.strip():
        return blob.strip()
    if not isinstance(blob, dict):
        return ""
    name = manager_name(blob)
    if name:
        return name
    manager = blob.get("manager")
    if isinstance(manager, dict):
        return manager_name(manager)
    team = blob.get("team")
    if isinstance(team, dict):
        return _nested_manager_name(team)
    return ""


def market_owner_name(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    direct = item.get("ownerName") or manager_name(item)
    if direct:
        return str(direct)
    for key in (
        "seller",
        "sellerTeam",
        "fromTeam",
        "previousTeam",
        "originTeam",
        "team",
        "playerTeam",
        "owner",
    ):
        name = _nested_manager_name(item.get(key))
        if name:
            return name
    return ""


def market_listing_type(item: dict[str, Any] | None) -> str:
    discr = fold(str((item or {}).get("discr") or ""))
    if "playerteam" in discr or discr.endswith("team"):
        return "sale"
    if "league" in discr:
        return "free"
    return "sale" if market_owner_name(item) else "free"


def market_player_team_id(item: dict[str, Any] | None) -> str | None:
    if not item:
        return None
    raw = item.get("playerTeamId")
    if raw is not None:
        return str(raw)
    nested = item.get("playerTeam")
    if isinstance(nested, dict) and nested.get("id") is not None:
        return str(nested["id"])
    if market_listing_type(item) == "sale" and item.get("id") is not None:
        return str(item["id"])
    return None


def history_actor_names(item: dict[str, Any] | None) -> list[str]:
    if not item:
        return []
    names: list[str] = []
    for value in (
        manager_name(item),
        item.get("sellerName"),
        item.get("buyerName"),
        item.get("managerName"),
        item.get("userName"),
        item.get("fromManager"),
        item.get("toManager"),
    ):
        if value:
            names.append(str(value))
    for key in (
        "seller",
        "buyer",
        "fromTeam",
        "toTeam",
        "previousTeam",
        "originTeam",
        "team",
        "sellerTeam",
        "buyerTeam",
        "manager",
    ):
        name = _nested_manager_name(item.get(key))
        if name:
            names.append(name)
    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = fold(name)
        if key and key not in seen:
            seen.add(key)
            unique.append(name)
    return unique


def enrich_history_item(
    item: dict[str, Any],
    events: list[dict[str, Any]],
    managers: dict[str, str],
    players: dict[str, str],
) -> dict[str, Any]:
    """Añade mánager al movimiento de /market/history cruzando con activity."""
    pid = player_id(item)
    amount = as_int(item.get("money") or item.get("amount") or item.get("price"))
    name = player_name(item) or str(item.get("playerName") or "")
    manager = None
    other = None
    actors = history_actor_names(item)
    if actors:
        manager = actors[0]
        other = actors[1] if len(actors) > 1 else None
    else:
        for event in events:
            if not isinstance(event, dict):
                continue
            evid = str(event.get("playerMasterId") or "") or (player_id(event) or "")
            ev_amount = as_int(event.get("amount") or event.get("money"))
            if amount and ev_amount and amount != ev_amount:
                continue
            ev_name = players.get(evid) or player_name(event) or str(event.get("playerName") or "")
            same_player = bool(pid and evid and pid == evid) or (
                bool(name) and fuzzy_match(name, ev_name)
            )
            if not same_player:
                continue
            actor_id = str(event.get("user1Id") or "")
            other_id = str(event.get("user2Id") or "")
            manager = managers.get(actor_id) or manager_name(event) or None
            other = managers.get(other_id) if other_id else None
            break
    return {
        "player": name,
        "playerId": pid,
        "operation": item.get("operation") or item.get("type") or item.get("discr"),
        "amount": amount,
        "date": item.get("date"),
        "manager": manager,
        "otherManager": other,
    }


def pick_player_status(player: dict[str, Any]) -> str:
    master = player.get("playerMaster") if isinstance(player.get("playerMaster"), dict) else player
    for key in ("playerStatus", "status", "state", "injuryStatus"):
        value = master.get(key) or player.get(key)
        if value:
            return str(value)
    return "unknown"
