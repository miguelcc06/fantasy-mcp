"""Serialización markdown/JSON de respuestas."""

from __future__ import annotations

import json
from typing import Any

from laliga_fantasy_mcp.models.schemas import ResponseFormat
from laliga_fantasy_mcp.config import DAILY_REWARD_FREE, POSITION_NAMES, STARTING_BUDGET
from laliga_fantasy_mcp.services.helpers import (
    as_int,
    extract_array,
    nested_player,
    player_id,
    player_name,
    unwrap,
)


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def format_money(value: Any) -> str:
    amount = as_int(value, 0)
    return f"{amount:,} €".replace(",", ".")


def format_points(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return f"{as_int(value)} pts"


def format_balances_markdown(payload: dict[str, Any]) -> str:
    """Saldo base vs techo de recompensas diarias, con la limitación explícita."""
    daily = format_money(DAILY_REWARD_FREE)
    start = format_money(payload.get("startingBudget") or STARTING_BUDGET)
    lines = [
        "# Saldos estimados",
        "",
        f"Todos parten de {start}. El **saldo base** reconstruye compras, ventas y cláusulas pagadas.",
        f"La **recompensa diaria** ({daily}/día) hay que reclamarla en la app y **no sale en el feed**:",
        "en rivales no se puede saber si la cobraron. El techo = base + (días en la liga × esa cuantía).",
        "Las pujas pendientes de rivales tampoco son visibles.",
        "",
    ]
    events = payload.get("activityEvents")
    if events is not None:
        lines.append(f"Eventos de actividad usados: {events}")
        lines.append("")
    for row in payload.get("balances") or []:
        if not isinstance(row, dict):
            continue
        name = row.get("managerName") or row.get("managerId") or "?"
        base = format_money(row.get("balanceBase") if row.get("balanceBase") is not None else row.get("balance"))
        rewards = as_int(row.get("maxDailyRewards") or row.get("dailyRewardCeiling"))
        days = as_int(row.get("dailyRewardDays"))
        ceiling = format_money(
            row.get("balanceMax") if row.get("balanceMax") is not None else as_int(row.get("balance")) + rewards
        )
        lines.append(f"## {name}")
        lines.append(f"- Saldo base: {base}")
        day_label = "día" if days == 1 else "días"
        lines.append(f"- Recompensas máximas posibles: {format_money(rewards)} ({days} {day_label})")
        lines.append(f"- Techo (base + recompensas): {ceiling}")
        lines.append(f"- Valor plantilla: {format_money(row.get('teamValue'))}")
        if row.get("clauseCost"):
            lines.append(f"- Cláusulas pagadas: -{format_money(row['clauseCost'])}")
        if row.get("officialCash") is not None:
            official = as_int(row.get("officialCash"))
            base_amt = as_int(row.get("balanceBase") if row.get("balanceBase") is not None else row.get("balance"))
            delta = official - base_amt
            note = "coincide con el base: no ha reclamado recompensas"
            if delta > 0 and rewards and delta <= rewards and delta % DAILY_REWARD_FREE == 0:
                claimed = delta // DAILY_REWARD_FREE
                noun = "recompensa diaria" if claimed == 1 else "recompensas diarias"
                note = f"implica {claimed} {noun} reclamada{'s' if claimed != 1 else ''}"
            elif delta:
                note = f"Δ {format_money(delta)} vs base"
            lines.append(f"- Saldo oficial (/money): {format_money(official)} ({note})")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_offers_markdown(name: str, owner: str | None, offers: Any, listing: str | None = None) -> str:
    lines = [f"# Ofertas por {name}", ""]
    if owner:
        suffix = " (venta)" if listing == "sale" else ""
        lines.append(f"Dueño: {owner}{suffix}")
        lines.append("")
    if not offers:
        lines.append("Sin ofertas pendientes.")
    else:
        lines.append("```json")
        lines.append(dumps(offers) if not isinstance(offers, str) else str(offers))
        lines.append("```")
    return "\n".join(lines)


def format_formation(value: Any) -> str:
    if value is None or value == "":
        return "n/d"
    if isinstance(value, list):
        if not value:
            return "n/d"
        if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
            return "-".join(str(int(item)) for item in value)
        return ", ".join(format_formation(item) for item in value)
    if isinstance(value, dict):
        return format_formation(
            value.get("tacticalFormation")
            or value.get("tactical_formation")
            or value.get("name")
            or value.get("key")
            or value.get("formation")
        )
    text = str(value).strip()
    parts = [part.strip() for part in text.split(",")]
    if len(parts) >= 2 and all(part.isdigit() for part in parts):
        return "-".join(parts)
    return text


def emit(payload: Any, response_format: ResponseFormat, markdown: str) -> str:
    if response_format == ResponseFormat.JSON:
        return dumps(payload)
    return markdown


def md_list(rows: list[str]) -> str:
    return "\n".join(f"- {row}" for row in rows) if rows else "- (sin datos)"


def _minutes_played(item: dict[str, Any], master: dict[str, Any]) -> int:
    raw = item.get("mins_played") or item.get("minutes") or master.get("mins_played") or master.get("minutes")
    if isinstance(raw, list) and raw:
        return as_int(raw[0])
    return as_int(raw)


def _stats_side(team: Any) -> dict[str, Any]:
    if not isinstance(team, dict):
        return {"name": str(team or "") or "n/d", "players": []}
    players: list[dict[str, Any]] = []
    for item in team.get("players") or []:
        if not isinstance(item, dict):
            continue
        master = nested_player(item)
        pos = as_int(item.get("positionId") or master.get("positionId"))
        if pos not in POSITION_NAMES:
            continue
        minutes = _minutes_played(item, master)
        players.append(
            {
                "id": player_id(item) or item.get("id"),
                "name": player_name(item) or item.get("nickname") or item.get("name"),
                "position": POSITION_NAMES.get(pos, "?"),
                "positionId": pos,
                "weekPoints": as_int(item.get("weekPoints") or master.get("weekPoints")),
                "minutes": minutes,
            }
        )
    players.sort(key=lambda row: (-as_int(row.get("weekPoints")), str(row.get("name") or "")))
    return {
        "name": team.get("mainName") or team.get("name") or team.get("shortName") or "n/d",
        "players": players,
    }


def compact_week_stats(week: int, stats: Any) -> dict[str, Any]:
    payload = unwrap(stats)
    matches = payload if isinstance(payload, list) else extract_array(payload)
    if not matches and isinstance(payload, dict):
        for key in ("stats", "matches", "weeks"):
            value = payload.get(key)
            if isinstance(value, list):
                matches = value
                break
    compact: list[dict[str, Any]] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        local = _stats_side(match.get("local") or match.get("home"))
        visitor = _stats_side(match.get("visitor") or match.get("away"))
        compact.append(
            {
                "date": match.get("date") or match.get("matchDate"),
                "local": local,
                "visitor": visitor,
            }
        )
    return {"week": week, "matches": compact}


def _played_in_week(player: dict[str, Any]) -> bool:
    return as_int(player.get("weekPoints")) != 0 or as_int(player.get("minutes")) > 0


def format_week_stats_markdown(summary: dict[str, Any]) -> str:
    week = summary.get("week")
    lines = [f"# Stats jornada {week}", ""]
    matches = summary.get("matches") or []
    if not matches:
        lines.append("Sin partidos con estadísticas.")
        return "\n".join(lines)
    for match in matches:
        local = match.get("local") or {}
        visitor = match.get("visitor") or {}
        date = match.get("date")
        heading = f"## {local.get('name') or '?'} vs {visitor.get('name') or '?'}"
        if date:
            heading += f" ({date})"
        lines.append(heading)
        for side in (local, visitor):
            club = side.get("name") or "?"
            players = [player for player in (side.get("players") or []) if _played_in_week(player)]
            if not players:
                lines.append(f"- {club}: sin jugadores")
                continue
            for player in players:
                pts = player.get("weekPoints")
                pos = player.get("position") or "?"
                lines.append(f"- {player.get('name')} ({club}, {pos}) — {pts} pts")
        lines.append("")
    return "\n".join(lines).rstrip()
