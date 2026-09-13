"""Registro de tools MCP de solo lectura."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ValidationError
from pydantic_core import PydanticUndefined

from laliga_fantasy_mcp.client.api import FantasyAPIError
from laliga_fantasy_mcp.config import (
    CURRENT_LALIGA_SLUGS,
    DAILY_REWARD_FREE,
    LALIGA_TEAMS,
    POSITION_NAMES,
    STARTING_BUDGET,
    TEAM_VALUE_BID_BONUS,
    ACTIVITY_LABELS,
)
from laliga_fantasy_mcp.context import LeagueContext
from laliga_fantasy_mcp.deps import api, ff
from laliga_fantasy_mcp.models.schemas import (
    ActivityInput,
    EmptyInput,
    FixturesInput,
    LineupInput,
    LineupsInput,
    MarketHistoryInput,
    OwnershipInput,
    PlayerOffersInput,
    PlayerQueryInput,
    ResponseFormat,
    RivalsInput,
    SearchPlayersInput,
    TrendsInput,
    WeekInput,
)
from laliga_fantasy_mcp.services.balances import compute_balances, load_activity, load_clause_snapshots
from laliga_fantasy_mcp.services.formatters import (
    compact_week_stats,
    emit,
    format_balances_markdown,
    format_formation,
    format_money,
    format_offers_markdown,
    format_points,
    format_week_stats_markdown,
)
from laliga_fantasy_mcp.services.helpers import (
    as_int,
    as_money,
    club_name,
    fold,
    fuzzy_match,
    is_current_laliga_slug,
    manager_name,
    merge_market_history,
    resolve_team_slug,
    market_listing_type,
    market_owner_name,
    market_player_team_id,
    nested_player,
    pick_player_status,
    player_id,
    player_name,
    position_id,
    lineup_week_points,
    team_id_from,
    unwrap,
)
from laliga_fantasy_mcp.services.ownership import build_ownership, _players_from_team
from laliga_fantasy_mcp.services.player_stats import enrich_player, enrich_player_page, find_players, summarize_player

READ_ONLY = {
    "title": "",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def _ann(title: str) -> dict[str, Any]:
    return {**READ_ONLY, "title": title}


def _field_constraints(field: Any) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for meta in field.metadata or ():
        for attr in ("ge", "gt", "le", "lt", "min_length", "max_length"):
            value = getattr(meta, attr, None)
            if value is not None:
                extra[attr] = value
    return extra


def _format_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in (err.get("loc") or ()) if item != "body")
        msg = err.get("msg") or "valor inválido"
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "Error: " + "; ".join(parts)


def flatten(model_cls: type[BaseModel]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Expone los campos Pydantic como argumentos planos de la tool."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        async def wrapped(**kwargs: Any) -> str:
            try:
                return await fn(model_cls.model_validate(kwargs))
            except ValidationError as exc:
                return _format_validation_error(exc)

        parameters: list[inspect.Parameter] = []
        annotations: dict[str, Any] = {"return": str}
        for name, field in model_cls.model_fields.items():
            extra: dict[str, Any] = _field_constraints(field)
            if field.description:
                extra["description"] = field.description
            if field.is_required():
                default: Any = Field(..., **extra)
            else:
                value = field.default
                if value is PydanticUndefined:
                    value = None
                default = Field(default=value, **extra)
            parameters.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=default,
                    annotation=field.annotation,
                )
            )
            annotations[name] = field.annotation
        wrapped.__signature__ = inspect.Signature(parameters, return_annotation=str)
        wrapped.__name__ = fn.__name__
        wrapped.__qualname__ = fn.__qualname__
        wrapped.__doc__ = fn.__doc__
        wrapped.__annotations__ = annotations
        return wrapped

    return decorator


def _error(exc: Exception) -> str:
    if isinstance(exc, FantasyAPIError):
        return str(exc)
    return f"Error: {exc}"


LINEUP_SLOTS = ("goalkeeper", "defender", "midfield", "striker")
SLOT_POSITION = {"goalkeeper": 1, "defender": 2, "midfield": 3, "striker": 4}


def _clone_lineup_item(item: dict[str, Any], *, slot: str | None = None) -> dict[str, Any]:
    cloned = dict(item)
    if slot:
        cloned["_slot"] = slot
    return cloned


def _is_fantasy_bench(item: dict[str, Any]) -> bool:
    return bool(item.get("bench") or item.get("substitute") or item.get("isBench"))


def _starter_items(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and not _is_fantasy_bench(item):
            out.append(item)
    return out


def _lineup_players(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    data = unwrap(payload) if isinstance(payload, dict) else payload
    if isinstance(data, list):
        return _starter_items(data)
    if not isinstance(data, dict):
        return []
    for key in ("players", "lineupPlayers", "playerTeams"):
        value = data.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return _starter_items(value)
    formation = data.get("formation")
    if isinstance(formation, dict):
        if isinstance(formation.get("players"), list):
            return _starter_items(formation["players"])
        players: list[dict[str, Any]] = []
        for slot in LINEUP_SLOTS:
            group = formation.get(slot)
            if not isinstance(group, list):
                continue
            for item in group:
                if isinstance(item, dict):
                    players.append(_clone_lineup_item(item, slot=slot))
        return players
    return []


def _summarize_lineup(payload: Any) -> dict[str, Any]:
    players = []
    for item in _lineup_players(payload):
        master = nested_player(item)
        pos = position_id(
            item.get("position")
            or item.get("positionId")
            or master.get("positionId")
            or SLOT_POSITION.get(str(item.get("_slot") or ""))
        )
        week_pts = lineup_week_points(item)
        players.append(
            {
                "id": player_id(item),
                "name": player_name(item),
                "positionId": pos,
                "position": POSITION_NAMES.get(pos or 0),
                "weekPoints": week_pts,
            }
        )
    formation = None
    if isinstance(payload, dict):
        raw = payload.get("formation") or (payload.get("lineup") or {}).get("formation")
        if isinstance(raw, dict):
            formation = format_formation(raw.get("tacticalFormation") or raw)
        elif raw:
            labeled = format_formation(raw)
            formation = None if labeled == "n/d" else labeled
    snapshot_points = None
    if isinstance(payload, dict):
        snapshot_points = payload.get("points")
        if snapshot_points is None:
            snapshot_points = payload.get("initialPoints")
    return {
        "formation": formation,
        "starters": players,
        "weekPoints": snapshot_points,
        "empty": payload is None,
    }


def _attach_season_to_lineup(lineup_info: dict[str, Any], roster: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {str(player.get("id")): player for player in roster if player.get("id") is not None}
    for starter in lineup_info.get("starters") or []:
        src = by_id.get(str(starter.get("id") or ""))
        if src:
            starter["seasonPoints"] = src.get("seasonPoints", src.get("points"))
    return lineup_info


async def _fetch_lineup(client: Any, team_id: str, week: int | None = None) -> tuple[Any, str | None]:
    try:
        return await client.lineup(team_id, week), None
    except FantasyAPIError as exc:
        if exc.status_code == 403:
            return (
                None,
                "La API oculta el XI editable de este rival (403). "
                "Usa lineup/week/{jornada} (como LaLigaApp) para ver el snapshot.",
            )
        raise


def _team_slug(query: str) -> str | None:
    return resolve_team_slug(query)


def _team_not_in_season_message(slug: str) -> str:
    info = LALIGA_TEAMS.get(slug) or {}
    label = info.get("fullName") or info.get("name") or slug
    return f"{label} no está en LaLiga esta temporada."


async def _slugs_from_calendar() -> list[str]:
    try:
        ctx = LeagueContext(api())
        week = await ctx.current_week_number()
        calendar = await api().calendar(week)
        matches = await ctx.enrich_calendar(calendar)
    except Exception:
        return list(CURRENT_LALIGA_SLUGS)
    slugs: list[str] = []
    seen: set[str] = set()
    for match in matches:
        for side in (match.get("local"), match.get("visitor")):
            slug = resolve_team_slug(str(side or ""))
            if slug and is_current_laliga_slug(slug) and slug not in seen:
                seen.add(slug)
                slugs.append(slug)
    if len(slugs) < 10:
        return list(CURRENT_LALIGA_SLUGS)
    return slugs


def register_tools(mcp: FastMCP) -> None:
    @mcp.tool(name="laliga_get_my_team", annotations=_ann("Plantilla y alineación propias"))
    @flatten(EmptyInput)
    async def laliga_get_my_team(params: EmptyInput) -> str:
        """Devuelve la plantilla, alineación, valor y saldo del usuario autenticado.

        Incluye jugadores, posiciones, valor de mercado, puntos, XI (titulares
        y formación) y dinero disponible para pujar (saldo + 20% del valor del equipo).

        Args:
            params (EmptyInput): league_id opcional y response_format.

        Returns:
            str: Markdown o JSON con plantilla, alineación y presupuesto.
        """
        try:
            client = api()
            ctx = LeagueContext(client)
            league_id = await ctx.league_id(params.league_id)
            team_id = await ctx.my_team_id(league_id)
            teams = await ctx.teams_master()
            team, money, lineup = await asyncio.gather(
                client.team(league_id, team_id),
                client.team_money(team_id),
                client.current_lineup(team_id),
            )
            players = [
                summarize_player(item, teams) for item in _players_from_team(team) if isinstance(item, dict)
            ]
            team_value = as_int(
                (team.get("teamValue") if isinstance(team, dict) else 0)
                or ((team.get("team") or {}).get("teamValue") if isinstance(team, dict) else 0)
            )
            cash = as_money(money)
            bid_budget = cash + int(team_value * TEAM_VALUE_BID_BONUS)
            lineup_info = _attach_season_to_lineup(_summarize_lineup(lineup), players)
            payload = {
                "leagueId": league_id,
                "teamId": team_id,
                "teamValue": team_value,
                "cash": cash,
                "bidBudget": bid_budget,
                "players": players,
                "lineup": lineup_info,
            }
            lines = [
                f"# Tu equipo (liga {league_id})",
                "",
                f"- Valor: {format_money(team_value)}",
                f"- Saldo: {format_money(cash)}",
                f"- Disponible para pujas (saldo + 20% valor): {format_money(bid_budget)}",
                f"- Formación: {lineup_info.get('formation') or 'n/d'}",
                "",
                "## Alineación",
            ]
            if not lineup_info["starters"]:
                lines.append("- Sin alineación publicada para esta jornada.")
            for player in lineup_info["starters"]:
                week_txt = f" · J {format_points(player.get('weekPoints'))}" if player.get("weekPoints") is not None else ""
                lines.append(f"- {player['name']} — {player.get('position') or '?'}{week_txt}")
            lines.extend(["", "## Plantilla", ""])
            for player in players:
                lines.append(
                    f"- {player['name']} ({POSITION_NAMES.get(player['positionId'] or 0, '?')}) "
                    f"{format_money(player['marketValue'])} · {format_points(player['points'])} · {player.get('status')}"
                )
            return emit(payload, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_standings", annotations=_ann("Clasificación general"))
    @flatten(EmptyInput)
    async def laliga_get_standings(params: EmptyInput) -> str:
        """Clasificación general de la liga privada.

        Args:
            params (EmptyInput): league_id opcional y response_format.

        Returns:
            str: Posición, mánager, puntos, puntos de jornada y valor.
        """
        try:
            ctx = LeagueContext(api())
            league_id = await ctx.league_id(params.league_id)
            standings = await ctx.standings(league_id)
            rows = []
            for index, entry in enumerate(standings, start=1):
                if not isinstance(entry, dict):
                    continue
                rows.append(
                    {
                        "position": entry.get("position") or index,
                        "manager": manager_name(entry),
                        "teamId": team_id_from(entry),
                        "points": as_int(entry.get("points")),
                        "livePoints": as_int(entry.get("livePoints") or entry.get("weekPoints")),
                        "teamValue": as_int(entry.get("teamValue") or (entry.get("team") or {}).get("teamValue")),
                    }
                )
            lines = [f"# Clasificación (liga {league_id})", ""]
            for row in rows:
                live = ""
                if row["livePoints"] and row["livePoints"] != row["points"]:
                    live = f" (en juego {row['livePoints']})"
                lines.append(
                    f"{row['position']}. {row['manager']} — {row['points']} pts"
                    f"{live} · {format_money(row['teamValue'])}"
                )
            return emit({"leagueId": league_id, "standings": rows}, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_rivals_teams", annotations=_ann("Plantillas rivales"))
    @flatten(RivalsInput)
    async def laliga_get_rivals_teams(params: RivalsInput) -> str:
        """Lista o consulta plantillas rivales y su última alineación.

        Args:
            params (RivalsInput): query opcional (mánager/equipo) y league_id.

        Returns:
            str: Plantilla y alineación del rival o listado de mánagers.
        """
        try:
            client = api()
            ctx = LeagueContext(client)
            league_id = await ctx.league_id(params.league_id)
            standings = await ctx.standings(league_id)
            if not params.query:
                listing = [
                    {
                        "manager": manager_name(entry),
                        "teamId": team_id_from(entry),
                        "points": as_int(entry.get("points")),
                    }
                    for entry in standings
                    if isinstance(entry, dict)
                ]
                lines = [f"# Rivales (liga {league_id})", ""] + [
                    f"- {item['manager']} (team {item['teamId']}) — {item['points']} pts" for item in listing
                ]
                return emit({"rivals": listing}, params.response_format, "\n".join(lines))
            entry = await ctx.find_team(league_id, params.query)
            tid = team_id_from(entry)
            teams = await ctx.teams_master()
            week = await ctx.current_week_number()
            team, (lineup, lineup_note) = await asyncio.gather(
                client.team(league_id, tid or ""),
                _fetch_lineup(client, tid or "", week),
            )
            players = [summarize_player(item, teams) for item in _players_from_team(team) if isinstance(item, dict)]
            lineup_info = _attach_season_to_lineup(_summarize_lineup(lineup), players)
            payload = {
                "manager": manager_name(entry),
                "teamId": tid,
                "week": week,
                "players": players,
                "lineup": lineup_info,
                "lineupNote": lineup_note,
            }
            lines = [f"# {payload['manager']} (jornada {week})", ""]
            if lineup_note:
                lines.extend([lineup_note, ""])
            if lineup_info["starters"]:
                lines.append("## Alineación")
                for player in lineup_info["starters"]:
                    lines.append(f"- {player['name']} — {player.get('position') or '?'}")
                lines.append("")
            lines.append("## Plantilla")
            for player in players:
                lines.append(
                    f"- {player['name']} · {format_money(player['marketValue'])} · {format_points(player['points'])}"
                )
            return emit(payload, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_calculate_rivals_balances", annotations=_ann("Saldos estimados de rivales"))
    @flatten(EmptyInput)
    async def laliga_calculate_rivals_balances(params: EmptyInput) -> str:
        """Estima el saldo de cada mánager: base (mercado + cláusulas) y techo de recompensas.

        Todos parten de 100.000.000 €. El saldo base recorre la actividad y resta
        las subidas de cláusula pagadas (1 € por cada 2 € de incremento). La
        recompensa diaria (100.000 €/día) hay que reclamarla en la app y no sale
        en el feed: en rivales no se puede saber si la cobraron. Se informa
        maxDailyRewards y balanceMax = base + ese techo. El saldo propio se
        contrasta con /money. Las pujas rivales no son visibles.

        Args:
            params (EmptyInput): league_id opcional.

        Returns:
            str: Saldo base, recompensas máximas posibles, techo y desvío propio.
        """
        try:
            client = api()
            ctx = LeagueContext(client)
            league_id = await ctx.league_id(params.league_id)
            standings = await ctx.standings(league_id)
            events = await load_activity(client, league_id)
            snapshots = await load_clause_snapshots(client, league_id, standings, events)
            rows = compute_balances(standings, events, clause_snapshots=snapshots)
            own_cash = None
            own_delta = None
            try:
                my_id = await ctx.my_team_id(league_id)
                own_cash = as_money(await client.team_money(my_id))
                for row in rows:
                    if row.get("teamId") == my_id:
                        base = row.get("balanceBase", row["balance"])
                        own_delta = own_cash - base
                        row["officialCash"] = own_cash
                        row["deltaVsOfficial"] = own_delta
            except Exception:
                pass
            payload = {
                "startingBudget": STARTING_BUDGET,
                "activityEvents": len(events),
                "dailyRewardPerDay": DAILY_REWARD_FREE,
                "notes": [
                    "Saldo base = mercado + cláusulas pagadas. No incluye recompensa diaria.",
                    "En rivales no se puede saber si reclamaron la recompensa diaria (no sale en el feed ni en /money).",
                    f"maxDailyRewards = días en la liga × {DAILY_REWARD_FREE:,} €. balanceMax = base + maxDailyRewards.".replace(",", "."),
                    "Las pujas pendientes de rivales no son visibles.",
                ],
                "balances": rows,
                "ownOfficialCash": own_cash,
                "ownDelta": own_delta,
            }
            return emit(payload, params.response_format, format_balances_markdown(payload))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_player_details", annotations=_ann("Ficha de jugador"))
    @flatten(PlayerQueryInput)
    async def laliga_get_player_details(params: PlayerQueryInput) -> str:
        """Ficha de un jugador por ID o nombre: puntos, valor, estado y stats.

        Args:
            params (PlayerQueryInput): player_id_or_name y league_id opcional.

        Returns:
            str: Historial de puntos, evolución de valor y estado actual.
        """
        try:
            client = api()
            ctx = LeagueContext(client)
            league_id = await ctx.league_id(params.league_id)
            catalog = await ctx.players()
            teams = await ctx.teams_master()
            matches = find_players(catalog, params.player_id_or_name)
            if not matches:
                return f"No se encontró ningún jugador que coincida con '{params.player_id_or_name}'."
            detailed = await enrich_player(client, matches[0], league_id)
            summary = summarize_player(detailed, teams)
            payload = {"query": params.player_id_or_name, "player": summary, "alternatives": [
                {"id": player_id(item), "name": player_name(item)} for item in matches[1:4]
            ]}
            lines = [
                f"# {summary['name']} ({summary.get('id')})",
                f"- Equipo: {summary.get('team') or 'n/d'}",
                f"- Posición: {POSITION_NAMES.get(summary.get('positionId') or 0, '?')}",
                f"- Valor: {format_money(summary.get('marketValue'))}",
                f"- Puntos: {format_points(summary.get('points'))} (media {summary.get('averagePoints')})",
                f"- Estado: {summary.get('status')}",
            ]
            week_points = summary.get("weekPoints") or []
            if isinstance(week_points, list) and week_points:
                lines.extend(["", "## Puntos por jornada"])
                for week in week_points[:10]:
                    if isinstance(week, dict):
                        lines.append(f"- J{week.get('weekNumber')}: {week.get('points')} pts")
            return emit(payload, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_probable_lineups", annotations=_ann("Onces probables"))
    @flatten(LineupsInput)
    async def laliga_get_probable_lineups(params: LineupsInput) -> str:
        """Onces probables de FútbolFantasy, con % de titularidad y bajas.

        Args:
            params (LineupsInput): team_or_match opcional (equipo o 'Local vs Visitante').

        Returns:
            str: Titulares, suplentes y ausencias.
        """
        try:
            slugs: list[str] | None = None
            if params.team_or_match:
                text = params.team_or_match
                if " vs " in fold(text) or " - " in text:
                    parts = [p.strip() for p in re_split_match(text)]
                    resolved: list[str] = []
                    notes: list[str] = []
                    for part in parts:
                        slug = _team_slug(part)
                        if not slug:
                            notes.append(f"No reconozco el equipo '{part}'.")
                        elif not is_current_laliga_slug(slug):
                            notes.append(_team_not_in_season_message(slug))
                        elif slug not in resolved:
                            resolved.append(slug)
                    if not resolved:
                        return " ".join(notes) or (
                            f"No reconozco el enfrentamiento '{text}'. Usa 'Local vs Visitante'."
                        )
                    slugs = resolved
                else:
                    slug = _team_slug(text)
                    if not slug:
                        return f"No reconozco el equipo '{text}'. Usa el slug (ej. real-madrid) o el nombre."
                    if not is_current_laliga_slug(slug):
                        return _team_not_in_season_message(slug)
                    slugs = [slug]
            else:
                slugs = await _slugs_from_calendar()
            lineups = await ff().all_lineups(slugs)
            lines = ["# Onces probables (FútbolFantasy)", ""]
            for item in lineups:
                lines.append(f"## {item.get('team') or item.get('slug')}")
                if item.get("error"):
                    lines.append(f"- Error: {item['error']}")
                    continue
                if item.get("note") and not item.get("starters"):
                    lines.append(f"- {item['note']}")
                for player in item.get("starters") or []:
                    pct = f" {player['probability']}%" if player.get("probability") is not None else ""
                    lines.append(f"- {player['name']}{pct} [{player.get('status')}]")
                bench = item.get("bench") or []
                if bench:
                    lines.append("### Suplentes")
                    for player in bench[:12]:
                        pct = f" {player['probability']}%" if player.get("probability") is not None else ""
                        lines.append(f"- {player['name']}{pct} [{player.get('status')}]")
                if item.get("absences"):
                    lines.append(f"- Bajas: {', '.join(item['absences'][:8])}")
                lines.append("")
            return emit({"lineups": lineups}, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_fixtures", annotations=_ann("Enfrentamientos de jornada"))
    @flatten(FixturesInput)
    async def laliga_get_fixtures(params: FixturesInput) -> str:
        """Enfrentamientos de la próxima jornada o de una jornada concreta.

        Args:
            params (FixturesInput): week opcional (si se omite, jornada actual/siguiente).

        Returns:
            str: Lista de partidos con local, visitante y horario.
        """
        try:
            ctx = LeagueContext(api())
            week = params.week or await ctx.current_week_number()
            calendar = await api().calendar(week)
            matches = await ctx.enrich_calendar(calendar)
            matches = sorted(matches, key=lambda item: str(item.get("date") or "9999"))
            lines = [f"# Jornada {week}", ""]
            for match in matches:
                lines.append(
                    f"- {match.get('local')} vs {match.get('visitor')}"
                    + (f" ({match.get('date')})" if match.get("date") else "")
                )
            return emit({"week": week, "matches": matches}, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_market", annotations=_ann("Mercado actual"))
    @flatten(EmptyInput)
    async def laliga_get_market(params: EmptyInput) -> str:
        """Mercado actual de la liga: libres, ventas de rivales y pujas visibles.

        Args:
            params (EmptyInput): league_id opcional.

        Returns:
            str: Jugadores en mercado con precio y dueño.
        """
        try:
            ctx = LeagueContext(api())
            league_id = await ctx.league_id(params.league_id)
            standings = await ctx.standings(league_id)
            items, ownership = await asyncio.gather(
                api().market(league_id),
                build_ownership(api(), league_id, standings),
            )
            owners_by_pid = {row["playerId"]: row["owner"] for row in ownership if row.get("playerId")}
            rows = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                master = item.get("playerMaster") if isinstance(item.get("playerMaster"), dict) else {}
                pid = player_id(item)
                listing = market_listing_type(item)
                owner = market_owner_name(item) or (owners_by_pid.get(pid) if pid else None) or ""
                if listing == "sale":
                    label = f"{owner} (venta)" if owner else "Venta rival"
                else:
                    label = owner or "Libre"
                num_bids = as_int(item.get("numberOfBids")) if item.get("numberOfBids") is not None else None
                rows.append(
                    {
                        "player": player_name(item),
                        "playerId": pid,
                        "owner": owner or None,
                        "listing": listing,
                        "label": label,
                        "price": as_money(item.get("salePrice") or item.get("price") or master.get("marketValue")),
                        "numberOfBids": num_bids,
                        "bid": (item.get("bid") or {}).get("money") if isinstance(item.get("bid"), dict) else None,
                        "discr": item.get("discr"),
                        "playerTeamId": market_player_team_id(item),
                    }
                )
            lines = [f"# Mercado (liga {league_id})", ""]
            for row in rows:
                bids_txt = ""
                if row.get("numberOfBids") is not None:
                    count = row["numberOfBids"]
                    noun = "puja" if count == 1 else "pujas"
                    bids_txt = f" · {count} {noun}"
                bid = f" (tu puja: {format_money(row['bid'])})" if row.get("bid") else ""
                lines.append(f"- {row['player']} · {format_money(row['price'])} · {row['label']}{bids_txt}{bid}")
            return emit({"leagueId": league_id, "items": rows}, params.response_format, "\n".join(lines) or "Mercado vacío.")
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_search_players", annotations=_ann("Buscar jugadores"))
    @flatten(SearchPlayersInput)
    async def laliga_search_players(params: SearchPlayersInput) -> str:
        """Busca en el catálogo de LaLiga Fantasy con filtros.

        Args:
            params (SearchPlayersInput): query, posición, equipo, estado, rango de valor, paginación.

        Returns:
            str: Lista paginada de jugadores.
        """
        try:
            client = api()
            ctx = LeagueContext(client)
            league_id = await ctx.league_id(params.league_id)
            catalog = await ctx.players()
            teams = await ctx.teams_master()
            if params.query:
                catalog = find_players(catalog, params.query, limit=max(len(catalog), 1))
            filtered: list[dict[str, Any]] = []
            pos = position_id(params.position) if params.position else None
            for player in catalog:
                if not isinstance(player, dict):
                    continue
                if pos and position_id(player.get("positionId") or player.get("position")) != pos:
                    continue
                club = club_name(player, teams)
                if params.team and not fuzzy_match(
                    params.team,
                    club,
                    str(player.get("teamId") or ""),
                    str((teams.get(str(player.get("teamId") or "")) or {}).get("slug") or ""),
                ):
                    continue
                status = pick_player_status(player)
                if params.status and fold(params.status) not in fold(status):
                    continue
                value = as_int(player.get("marketValue") or (player.get("playerMaster") or {}).get("marketValue"))
                if params.min_value is not None and value < params.min_value:
                    continue
                if params.max_value is not None and value > params.max_value:
                    continue
                filtered.append(player)
            total = len(filtered)
            offset = params.offset or 0
            limit = params.limit or 25
            raw_page = filtered[offset : offset + limit]
            enriched = await enrich_player_page(client, raw_page, league_id)
            page = [summarize_player(player, teams) for player in enriched]
            payload = {
                "total": total,
                "count": len(page),
                "offset": offset,
                "has_more": offset + len(page) < total,
                "next_offset": offset + len(page) if offset + len(page) < total else None,
                "players": page,
            }
            lines = [f"# Jugadores ({len(page)} de {total})", ""]
            for player in page:
                lines.append(
                    f"- {player['name']} ({player.get('id')}) · {player.get('team')} · "
                    f"{format_money(player['marketValue'])} · {format_points(player['points'])}"
                )
            return emit(payload, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_market_trends", annotations=_ann("Tendencias de valor"))
    @flatten(TrendsInput)
    async def laliga_get_market_trends(params: TrendsInput) -> str:
        """Subidas y bajadas diarias de valor según FútbolFantasy.

        Args:
            params (TrendsInput): filter all|rising|falling|stable, posición y limit.

        Returns:
            str: Ranking de cambios de valor.
        """
        try:
            trends = await ff().trends()
            pos = fold(params.position) if params.position else ""
            filtered = []
            for item in trends:
                if params.filter == "rising" and item["change"] <= 0:
                    continue
                if params.filter == "falling" and item["change"] >= 0:
                    continue
                if params.filter == "stable" and item["change"] != 0:
                    continue
                if pos and pos not in fold(item.get("position")):
                    continue
                filtered.append(item)
            filtered.sort(key=lambda item: abs(item["change"]), reverse=True)
            page = filtered[: params.limit or 30]
            lines = [f"# Tendencias de mercado ({params.filter})", ""]
            for item in page:
                sign = "+" if item["change"] > 0 else ""
                pct = item.get("changePercent")
                pct_txt = f"{pct:.2f}" if isinstance(pct, (int, float)) else str(pct)
                lines.append(
                    f"- {item['name']} ({item['team']}): {sign}{format_money(item['change'])} "
                    f"({pct_txt}%) → {format_money(item['value'])}"
                )
            return emit({"filter": params.filter, "players": page}, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_ownership", annotations=_ann("Dueños y cláusulas"))
    @flatten(OwnershipInput)
    async def laliga_get_ownership(params: OwnershipInput) -> str:
        """Mapa de dueños, cláusulas, bloqueos y blindajes de la liga.

        Args:
            params (OwnershipInput): query opcional (jugador, mánager, unlocked/locked).

        Returns:
            str: Filas de propiedad con cláusula y estado de bloqueo.
        """
        try:
            client = api()
            ctx = LeagueContext(client)
            league_id = await ctx.league_id(params.league_id)
            standings = await ctx.standings(league_id)
            rows = await build_ownership(client, league_id, standings)
            query = fold(params.query)
            if query:
                if query in {"unlocked", "libre", "libres"}:
                    rows = [row for row in rows if not row["clauseLocked"]]
                elif query in {"locked", "bloqueada", "bloqueadas"}:
                    rows = [row for row in rows if row["clauseLocked"]]
                else:
                    rows = [
                        row
                        for row in rows
                        if fuzzy_match(params.query or "", row["name"], row["owner"], row["playerId"])
                    ]
            page = rows[: params.limit or 50]
            lines = [f"# Ownership (liga {league_id})", ""]
            if not page:
                lines.append("Sin resultados para ese filtro.")
            for row in page:
                lock = "bloqueada" if row["clauseLocked"] else "disponible"
                shield = " · blindado" if row["isShielded"] else ""
                lines.append(
                    f"- {row['name']} → {row['owner']} · cláusula {format_money(row['buyoutClause'])} ({lock}){shield}"
                )
            return emit({"total": len(rows), "items": page}, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_player_offers", annotations=_ann("Ofertas sobre un jugador"))
    @flatten(PlayerOffersInput)
    async def laliga_get_player_offers(params: PlayerOffersInput) -> str:
        """Consulta ofertas/pujas sobre un jugador (playerTeamId o nombre).

        Args:
            params (PlayerOffersInput): player_id_or_name.

        Returns:
            str: Ofertas pendientes, o ficha de bolsa si el jugador está libre.
        """
        try:
            client = api()
            ctx = LeagueContext(client)
            league_id = await ctx.league_id(params.league_id)
            standings = await ctx.standings(league_id)
            ownership, market = await asyncio.gather(
                build_ownership(client, league_id, standings),
                client.market(league_id),
            )
            q = params.player_id_or_name
            owned = None
            for row in ownership:
                if q in {row["playerId"], row["playerTeamId"]} or fuzzy_match(q, row["name"]):
                    owned = row
                    break
            listing = None
            for item in market:
                if not isinstance(item, dict):
                    continue
                pid = player_id(item)
                ptid = market_player_team_id(item)
                if q in {pid, ptid} or fuzzy_match(q, player_name(item)):
                    listing = item
                    break
            if owned:
                offers = await client.player_offer(league_id, owned["playerTeamId"])
                payload = {"player": owned, "offers": offers, "listing": "owned"}
                return emit(
                    payload,
                    params.response_format,
                    format_offers_markdown(owned["name"], owned.get("owner"), offers),
                )
            if listing is not None:
                kind = market_listing_type(listing)
                owner = market_owner_name(listing)
                pid = player_id(listing)
                if not owner and pid:
                    for row in ownership:
                        if row["playerId"] == pid:
                            owner = row["owner"]
                            break
                price = as_money(
                    listing.get("salePrice") or listing.get("price") or (listing.get("playerMaster") or {}).get("marketValue")
                )
                num_bids = as_int(listing.get("numberOfBids")) if listing.get("numberOfBids") is not None else None
                bid = (listing.get("bid") or {}).get("money") if isinstance(listing.get("bid"), dict) else None
                name = player_name(listing)
                if kind == "free":
                    payload = {
                        "player": name,
                        "playerId": player_id(listing),
                        "listing": "free",
                        "price": price,
                        "numberOfBids": num_bids,
                        "bid": bid,
                        "offers": [],
                    }
                    bids_line = ""
                    if num_bids is not None:
                        noun = "puja activa" if num_bids == 1 else "pujas activas"
                        bids_line = f"\n- Pujas en mercado: {num_bids} {noun}"
                    bid_txt = f"\n- Tu puja: {format_money(bid)}" if bid else ""
                    return emit(
                        payload,
                        params.response_format,
                        f"# {name} está libre en el mercado\n\n"
                        "No tiene playerTeamId: las pujas de bolsa no se consultan por este endpoint.\n"
                        f"- Precio: {format_money(price)}{bids_line}{bid_txt}",
                    )
                ptid = market_player_team_id(listing)
                if not ptid:
                    return f"No se encontró playerTeamId para '{q}'. Prueba el nombre exacto o el ID."
                offers = await client.player_offer(league_id, ptid)
                payload = {
                    "player": name,
                    "playerId": player_id(listing),
                    "playerTeamId": ptid,
                    "owner": owner,
                    "listing": "sale",
                    "offers": offers,
                }
                owner_txt = owner or "rival"
                return emit(
                    payload,
                    params.response_format,
                    format_offers_markdown(name, owner_txt, offers, listing="sale"),
                )
            return f"'{q}' no está en tu liga ni en el mercado. Prueba el nombre exacto o el ID."
        except FantasyAPIError as exc:
            if exc.status_code == 403:
                return "Error: la API no permite ver ofertas de jugadores que no son tuyos (403)."
            return _error(exc)
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_league_activity", annotations=_ann("Actividad de la liga"))
    @flatten(ActivityInput)
    async def laliga_get_league_activity(params: ActivityInput) -> str:
        """Feed de actividad: compras, ventas, cláusulas y primas.

        Args:
            params (ActivityInput): page (0 = reciente), limit y offset opcional.

        Returns:
            str: Eventos de la liga.
        """
        try:
            ctx = LeagueContext(api())
            league_id = await ctx.league_id(params.league_id)
            events, managers, players = await asyncio.gather(
                load_activity(api(), league_id),
                ctx.manager_names(league_id),
                ctx.player_names(),
            )
            limit = params.limit or 40
            if params.offset is not None:
                start = params.offset
            else:
                start = (params.page or 0) * limit
            page = events[start : start + limit]
            rows = []
            for item in page:
                if not isinstance(item, dict):
                    continue
                atype = as_int(item.get("activityTypeId"))
                actor_id = str(item.get("user1Id") or "")
                other_id = str(item.get("user2Id") or "")
                pid = str(item.get("playerMasterId") or "")
                rows.append(
                    {
                        "typeId": atype,
                        "type": ACTIVITY_LABELS.get(atype, f"tipo {atype}"),
                        "manager": managers.get(actor_id) or manager_name(item) or actor_id,
                        "otherManager": managers.get(other_id) if other_id else None,
                        "player": players.get(pid) or player_name(item) or item.get("playerName"),
                        "amount": as_int(item.get("amount") or item.get("money")),
                        "when": item.get("createdAt") or item.get("timestamp"),
                    }
                )
            lines = [f"# Actividad p.{params.page or 0}", ""]
            if not rows:
                lines.append("Sin eventos en esta página.")
            for row in rows:
                amount = f" {format_money(row['amount'])}" if row["amount"] else ""
                player = f" a {row['player']}" if row["player"] else ""
                other = f" de {row['otherManager']}" if row.get("otherManager") and row["typeId"] in {1, 32} else ""
                lines.append(f"- {row['manager']} {row['type']}{player}{other}{amount}")
            return emit({"events": rows}, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_market_history", annotations=_ann("Histórico de mercado"))
    @flatten(MarketHistoryInput)
    async def laliga_get_market_history(params: MarketHistoryInput) -> str:
        """Histórico de movimientos de mercado de toda la liga (no solo del usuario).

        Combina /market/history con compras, ventas y cláusulas del feed de actividad.

        Args:
            params (MarketHistoryInput): limit y query opcional.

        Returns:
            str: Ventas/compras recientes de todos los mánagers.
        """
        try:
            ctx = LeagueContext(api())
            league_id = await ctx.league_id(params.league_id)
            history: list[Any] = []
            try:
                history = await api().market_history(league_id)
            except FantasyAPIError as exc:
                if exc.status_code != 404:
                    raise
            events, managers, players = await asyncio.gather(
                load_activity(api(), league_id),
                ctx.manager_names(league_id),
                ctx.player_names(),
            )
            rows = merge_market_history(history, events, managers, players)
            if params.query:
                rows = [
                    item
                    for item in rows
                    if fuzzy_match(
                        params.query,
                        item.get("player"),
                        str(item.get("operation") or ""),
                        item.get("manager"),
                        item.get("otherManager"),
                    )
                ]
            page = rows[: params.limit or 40]
            lines = [f"# Histórico de mercado ({len(page)})", ""]
            if not page:
                lines.append("Sin movimientos de mercado.")
            for item in page:
                actors = [name for name in (item.get("manager"), item.get("otherManager")) if name]
                actor_txt = f" · {' / '.join(actors)}" if actors else ""
                lines.append(
                    f"- {item.get('player') or '?'} · "
                    f"{item.get('operation') or ''}{actor_txt} · "
                    f"{format_money(item.get('amount'))}"
                )
            return emit({"items": page}, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_week_standings", annotations=_ann("Clasificación de jornada"))
    @flatten(WeekInput)
    async def laliga_get_week_standings(params: WeekInput) -> str:
        """Clasificación de una jornada concreta (no el acumulado).

        Args:
            params (WeekInput): week opcional (por defecto la actual).

        Returns:
            str: Puntos de esa jornada por mánager.
        """
        try:
            ctx = LeagueContext(api())
            league_id = await ctx.league_id(params.league_id)
            week = params.week or await ctx.current_week_number()
            standings = await api().standing_week(league_id, week)
            rows = []
            for index, entry in enumerate(standings, start=1):
                if not isinstance(entry, dict):
                    continue
                rows.append(
                    {
                        "position": entry.get("position") or index,
                        "manager": manager_name(entry),
                        "points": as_int(entry.get("points") or entry.get("livePoints")),
                    }
                )
            lines = [f"# Clasificación jornada {week}", ""] + [
                f"{row['position']}. {row['manager']} — {row['points']} pts" for row in rows
            ]
            return emit({"week": week, "standings": rows}, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_lineup", annotations=_ann("Alineación por jornada"))
    @flatten(LineupInput)
    async def laliga_get_lineup(params: LineupInput) -> str:
        """Alineación de un equipo en una jornada (snapshot público de la liga).

        Igual que LaLigaApp: GET /teams/{id}/lineup/week/{week}. El XI editable
        de un rival (sin jornada) está restringido a 403; el snapshot de jornada no.

        Args:
            params (LineupInput): team_or_manager ('me' por defecto) y week opcional.

        Returns:
            str: Titulares y formación.
        """
        try:
            client = api()
            ctx = LeagueContext(client)
            league_id = await ctx.league_id(params.league_id)
            query = params.team_or_manager or "me"
            if fold(query) in {"me", "yo", "mi equipo"}:
                tid = await ctx.my_team_id(league_id)
                label = "Tu equipo"
            else:
                entry = await ctx.find_team(league_id, query)
                tid = team_id_from(entry) or ""
                label = manager_name(entry)
            week = params.week or await ctx.current_week_number()
            payload, note = await _fetch_lineup(client, tid, week)
            info = _summarize_lineup(payload)
            info.update({"teamId": tid, "manager": label, "week": week, "note": note})
            heading = f"# Alineación de {label} (jornada {week})"
            if note:
                teams = await ctx.teams_master()
                team = await client.team(league_id, tid)
                players = [summarize_player(item, teams) for item in _players_from_team(team) if isinstance(item, dict)]
                info["players"] = players
                lines = [heading, "", note, "", "## Plantilla"]
                for player in players:
                    lines.append(f"- {player['name']} · {format_money(player['marketValue'])}")
                return emit(info, params.response_format, "\n".join(lines))
            if payload is None:
                return emit(
                    info,
                    params.response_format,
                    f"{heading}\n\nSin alineación para esta jornada (la API respondió 204 / vacío).",
                )
            lines = [heading, ""]
            if info.get("formation"):
                lines.append(f"Formación: {info['formation']}")
            if info.get("weekPoints") is not None:
                lines.append(f"Puntos de jornada: {info['weekPoints']}")
            lines.append("")
            if not info["starters"]:
                lines.append("- Sin jugadores en el XI.")
            for player in info["starters"]:
                week_txt = f" · {format_points(player.get('weekPoints'))}" if player.get("weekPoints") is not None else ""
                lines.append(f"- {player['name']}{week_txt}")
            return emit(info, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_match_stats", annotations=_ann("Stats de la jornada"))
    @flatten(WeekInput)
    async def laliga_get_match_stats(params: WeekInput) -> str:
        """Estadísticas oficiales de una jornada (minutos, goles, etc.).

        Args:
            params (WeekInput): week opcional.

        Returns:
            str: Stats o aviso si la jornada aún no ha empezado (404).
        """
        try:
            ctx = LeagueContext(api())
            week = params.week or await ctx.current_week_number()
            try:
                stats = await api().week_stats(week)
            except FantasyAPIError as exc:
                if exc.status_code == 404:
                    return f"Aún no hay estadísticas para la jornada {week} (la API respondió 404)."
                raise
            summary = compact_week_stats(week, stats)
            return emit(summary, params.response_format, format_week_stats_markdown(summary))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_formations", annotations=_ann("Formaciones legales"))
    @flatten(EmptyInput)
    async def laliga_get_formations(params: EmptyInput) -> str:
        """Formaciones tácticas admitidas por LaLiga Fantasy (free y premium).

        Args:
            params (EmptyInput): response_format.

        Returns:
            str: Lista de formaciones.
        """
        try:
            free, premium = await asyncio.gather(api().formations("free"), api().formations("premium"))
            payload = {
                "free": [format_formation(item) for item in (free or [])] if isinstance(free, list) else format_formation(free),
                "premium": [format_formation(item) for item in (premium or [])] if isinstance(premium, list) else format_formation(premium),
            }
            lines = ["# Formaciones", "", "## Free"]
            free_rows = payload["free"] if isinstance(payload["free"], list) else [payload["free"]]
            premium_rows = payload["premium"] if isinstance(payload["premium"], list) else [payload["premium"]]
            lines.extend(f"- {item}" for item in free_rows)
            lines.extend(["", "## Premium"])
            lines.extend(f"- {item}" for item in premium_rows)
            return emit(payload, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_injuries", annotations=_ann("Lesionados y sanciones"))
    @flatten(EmptyInput)
    async def laliga_get_injuries(params: EmptyInput) -> str:
        """Bajas, dudas y disponibles según FútbolFantasy.

        Args:
            params (EmptyInput): response_format.

        Returns:
            str: Listado de estados físicos.
        """
        try:
            rows = await ff().injuries()
            lines = ["# Lesionados y dudas (FútbolFantasy)", ""]
            for row in rows[:80]:
                pct = f" · {row['probability']}%" if row.get("probability") is not None else ""
                lines.append(f"- {row['name']} · {row['status']}{pct} · {row['detail'][:120]}")
            return emit({"players": rows}, params.response_format, "\n".join(lines) if rows else "Sin datos de bajas.")
        except Exception as exc:
            return _error(exc)


def re_split_match(text: str) -> list[str]:
    for sep in (" vs ", " VS ", " v ", " - "):
        if sep in text:
            return text.split(sep, 1)
    return [text]
