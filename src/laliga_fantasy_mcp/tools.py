"""Registro de tools MCP de LaLiga Fantasy."""

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
    ACTIVITY_LABELS,
    CURRENT_LALIGA_SLUGS,
    DAILY_REWARD_FREE,
    LALIGA_TEAMS,
    POSITION_NAMES,
    STARTING_BUDGET,
    TEAM_VALUE_BID_BONUS,
    get_bid_policy,
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
    PlaceBidInput,
    PlayerOffersInput,
    PlayerQueryInput,
    RealStandingsInput,
    ResponseFormat,
    RivalsInput,
    SanctionsInput,
    SearchPlayersInput,
    SetLineupInput,
    SetPieceTakersInput,
    SquadAggregateStatsInput,
    TrendsInput,
    TrimSoleBidsInput,
    UpdateBidInput,
    WeekInput,
)
from laliga_fantasy_mcp.services.balances import compute_balances, load_activity, load_clause_snapshots
from laliga_fantasy_mcp.services.bids import (
    bid_policy_error,
    build_trim_report,
    extract_user_bid,
    missing_player_team_id_error,
    plan_sole_bid_trims,
    resolve_bid_player,
)
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


WRITE_ACTION = {
    "title": "",
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def _ann_write(title: str) -> dict[str, Any]:
    return {**WRITE_ACTION, "title": title}


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
LINEUP_PUT_EXTRAS = ("coach", "captain", "bench")


def parse_tactical_formation(value: str) -> tuple[int, int, int] | None:
    """Convierte '4-4-2' / '4,4,2' / '1-4-4-2' en (DEF, MED, DEL)."""
    parts = [int(item) for item in value.replace("-", ",").split(",") if item.strip().isdigit()]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 4:
        return parts[1], parts[2], parts[3]
    return None


def lineup_player_team_id(item: dict[str, Any]) -> str | None:
    """playerTeamId del slot de plantilla, nunca el id de catálogo (playerMaster)."""
    master = item.get("playerMaster") if isinstance(item.get("playerMaster"), dict) else {}
    master_id = master.get("id")
    for key in ("playerTeamId", "id"):
        raw = item.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        if key == "id" and master_id is not None and str(raw) == str(master_id):
            continue
        return str(raw)
    return None


def _slot_id(value: str) -> str | int:
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def build_lineup_put_payload(
    *,
    goalkeeper: list[str],
    defender: list[str],
    midfield: list[str],
    striker: list[str],
    formation: tuple[int, int, int],
    captain_id: str | None = None,
    bench_ids: list[str] | None = None,
    coach_id: str | None = None,
) -> dict[str, Any]:
    """Body de PUT /teams/{id}/lineup usado por la app oficial.

    GET devuelve `{formation: {tacticalFormation, goalkeeper:[{playerTeamId}...]}}`.
    Reenviar esa forma provoca HTTP 500. El contrato de escritura es plano:

        goalkeeper: id, defender/midfield/striker: [ids], tactical_formation: [D,M,F]

    Capitán, banquillo y entrenador son premium; se omiten en ligas free.
    """
    if len(goalkeeper) != 1:
        raise ValueError("La alineación debe contener exactamente 1 portero.")
    payload: dict[str, Any] = {
        "goalkeeper": _slot_id(goalkeeper[0]),
        "defender": [_slot_id(item) for item in defender],
        "midfield": [_slot_id(item) for item in midfield],
        "striker": [_slot_id(item) for item in striker],
        "tactical_formation": [formation[0], formation[1], formation[2]],
    }
    if captain_id:
        payload["captain"] = str(captain_id)
    if coach_id:
        payload["coach"] = _slot_id(coach_id)
    if bench_ids:
        payload["bench"] = [_slot_id(item) for item in bench_ids]
    return payload


def _resolve_roster_entry(
    query: str,
    roster_by_id: dict[str, dict[str, Any]],
    roster_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    q = str(query).strip()
    match = roster_by_id.get(q) or roster_by_name.get(fold(q))
    if match:
        return match
    folded = fold(q)
    for name, entry in roster_by_name.items():
        if folded and folded in name:
            return entry
    return None


def prepare_set_lineup_payload(
    roster: list[Any],
    formation: str,
    starters: list[str],
    captain_id: str | None = None,
    bench: list[str] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    """Valida plantilla + formación y construye el body de PUT. Error en el 3er valor."""
    roster_by_id: dict[str, dict[str, Any]] = {}
    roster_by_name: dict[str, dict[str, Any]] = {}
    for player in roster:
        if not isinstance(player, dict):
            continue
        master = player.get("playerMaster") if isinstance(player.get("playerMaster"), dict) else player
        pid = str(master.get("id") or player.get("id") or "")
        ptid = lineup_player_team_id(player)
        pname = fold(master.get("name") or player.get("name") or "")
        pnick = fold(master.get("nickname") or player.get("nickname") or "")
        entry = {
            "player": player,
            "id": pid,
            "playerTeamId": ptid or "",
            "name": master.get("nickname") or master.get("name") or player.get("name"),
        }
        if pid:
            roster_by_id[pid] = entry
        if ptid:
            roster_by_id[ptid] = entry
        if pname:
            roster_by_name[pname] = entry
        if pnick:
            roster_by_name[pnick] = entry

    resolved_starters: list[dict[str, Any]] = []
    for item in starters:
        match = _resolve_roster_entry(str(item), roster_by_id, roster_by_name)
        if not match:
            return None, [], f"Error: El jugador '{item}' no pertenece a tu plantilla."
        if match in resolved_starters:
            return None, [], f"Error: El jugador '{match['name']}' está duplicado en los titulares."
        if not match["playerTeamId"]:
            return None, [], (
                f"Error: '{match['name']}' no tiene playerTeamId; no se puede alinear."
            )
        resolved_starters.append(match)

    if len(resolved_starters) != 11:
        return None, [], f"Error: Se requieren exactamente 11 titulares (recibidos {len(resolved_starters)})."

    parsed = parse_tactical_formation(formation)
    if parsed is None:
        return None, [], (
            f"Error: Formato de formación inválido '{formation}'. Usa ej. '4-4-2' o '3-5-2'."
        )
    exp_def, exp_mid, exp_str = parsed

    by_pos: dict[str, list[dict[str, Any]]] = {
        "goalkeeper": [],
        "defender": [],
        "midfield": [],
        "striker": [],
    }
    for entry in resolved_starters:
        player = entry["player"]
        master = player.get("playerMaster") if isinstance(player.get("playerMaster"), dict) else player
        pos = position_id(master.get("positionId") or player.get("positionId"))
        if pos == 1:
            by_pos["goalkeeper"].append(entry)
        elif pos == 2:
            by_pos["defender"].append(entry)
        elif pos == 3:
            by_pos["midfield"].append(entry)
        elif pos == 4:
            by_pos["striker"].append(entry)

    if len(by_pos["goalkeeper"]) != 1:
        return None, [], (
            f"Error: La alineación debe contener exactamente 1 portero "
            f"(tienes {len(by_pos['goalkeeper'])})."
        )
    if len(by_pos["defender"]) != exp_def:
        return None, [], (
            f"Error: La formación {formation} requiere {exp_def} defensas "
            f"(seleccionados {len(by_pos['defender'])})."
        )
    if len(by_pos["midfield"]) != exp_mid:
        return None, [], (
            f"Error: La formación {formation} requiere {exp_mid} centrocampistas "
            f"(seleccionados {len(by_pos['midfield'])})."
        )
    if len(by_pos["striker"]) != exp_str:
        return None, [], (
            f"Error: La formación {formation} requiere {exp_str} delanteros "
            f"(seleccionados {len(by_pos['striker'])})."
        )

    captain_ptid: str | None = None
    if captain_id:
        cap_match = _resolve_roster_entry(str(captain_id), roster_by_id, roster_by_name)
        if not cap_match:
            return None, [], f"Error: El capitán '{captain_id}' no pertenece a tu plantilla."
        if cap_match not in resolved_starters:
            return None, [], "Error: El capitán debe ser uno de los 11 titulares."
        captain_ptid = cap_match["playerTeamId"]

    bench_ids: list[str] | None = None
    if bench:
        bench_ids = []
        for item in bench:
            match = _resolve_roster_entry(str(item), roster_by_id, roster_by_name)
            if not match:
                return None, [], f"Error: El suplente '{item}' no pertenece a tu plantilla."
            if match in resolved_starters:
                return None, [], f"Error: '{match['name']}' no puede estar en titulares y banquillo."
            if not match["playerTeamId"]:
                return None, [], f"Error: '{match['name']}' no tiene playerTeamId."
            bench_ids.append(match["playerTeamId"])

    payload = build_lineup_put_payload(
        goalkeeper=[item["playerTeamId"] for item in by_pos["goalkeeper"]],
        defender=[item["playerTeamId"] for item in by_pos["defender"]],
        midfield=[item["playerTeamId"] for item in by_pos["midfield"]],
        striker=[item["playerTeamId"] for item in by_pos["striker"]],
        formation=parsed,
        captain_id=captain_ptid,
        bench_ids=bench_ids,
    )
    return payload, resolved_starters, None


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


async def laliga_place_bid(params: PlaceBidInput) -> str:
    """Coloca una puja nueva sobre un jugador del mercado.

    Solo la política ``create_and_update`` lo permite. ``readonly`` y ``update_own``
    rechazan la creación con un error que indica cómo cambiar LALIGA_FANTASY_BID_POLICY.

    Args:
        params (PlaceBidInput): player_team_id_or_name y amount en euros.

    Returns:
        str: Confirmación de la puja o error de política, resolución o API.
    """
    blocked = bid_policy_error("place")
    if blocked:
        return blocked
    try:
        client = api()
        ctx = LeagueContext(client)
        league_id = await ctx.league_id(params.league_id)
        listing, player_team_id, error = resolve_bid_player(
            await client.market(league_id),
            params.player_team_id_or_name,
        )
        if error:
            return error
        if listing is not None and extract_user_bid(listing) is not None:
            name = player_name(listing) or params.player_team_id_or_name
            return (
                f"Error: ya tienes una puja activa sobre {name}. "
                "Usa laliga_update_bid para cambiar el importe."
            )
        missing = missing_player_team_id_error(listing, player_team_id, params.player_team_id_or_name)
        if missing or not player_team_id:
            return missing or "Error: no se pudo resolver el playerTeamId."
        amount = int(params.amount)
        result = await client.place_bid(league_id, player_team_id, amount)
        policy = get_bid_policy()
        name = player_name(listing) if listing else params.player_team_id_or_name
        payload = {
            "status": "placed",
            "policy": policy,
            "leagueId": league_id,
            "player": name,
            "playerId": player_id(listing) if listing else None,
            "playerTeamId": player_team_id,
            "amount": amount,
            "raw": result,
        }
        lines = [
            "# Puja registrada",
            f"- Jugador: {name}",
            f"- playerTeamId: {player_team_id}",
            f"- Importe: {format_money(amount)}",
            f"- Política: {policy}",
        ]
        return emit(payload, params.response_format, "\n".join(lines))
    except Exception as exc:
        return _error(exc)


async def laliga_update_bid(params: UpdateBidInput) -> str:
    """Actualiza el importe de una puja activa del usuario.

    ``update_own`` y ``create_and_update`` lo permiten. ``readonly`` lo rechaza.
    Exige que el mercado muestre una puja tuya sobre ese jugador.

    Args:
        params (UpdateBidInput): jugador, nuevo importe y offer_id opcional.

    Returns:
        str: Confirmación de la actualización o error de política, titularidad o API.
    """
    blocked = bid_policy_error("update")
    if blocked:
        return blocked
    try:
        client = api()
        ctx = LeagueContext(client)
        league_id = await ctx.league_id(params.league_id)
        listing, player_team_id, error = resolve_bid_player(
            await client.market(league_id),
            params.player_team_id_or_name,
        )
        if error:
            return error
        if listing is None:
            return (
                f"Error: no hay una puja activa verificable sobre '{params.player_team_id_or_name}'. "
                "Solo se actualizan pujas que ya figuran a tu nombre en el mercado."
            )
        user_bid = extract_user_bid(listing)
        name = player_name(listing) or params.player_team_id_or_name
        if user_bid is None:
            return (
                f"Error: no tienes una puja activa sobre {name}. "
                "Con la política actual solo se actualizan pujas existentes del usuario."
            )
        missing = missing_player_team_id_error(listing, player_team_id, params.player_team_id_or_name)
        if missing or not player_team_id:
            return missing or "Error: no se pudo resolver el playerTeamId."
        offer_id = params.offer_id or user_bid.get("offerId")
        amount = int(params.amount)
        result = await client.update_bid(league_id, player_team_id, amount, offer_id=offer_id)
        policy = get_bid_policy()
        payload = {
            "status": "updated",
            "policy": policy,
            "leagueId": league_id,
            "player": name,
            "playerId": player_id(listing),
            "playerTeamId": player_team_id,
            "offerId": offer_id,
            "previousAmount": user_bid.get("amount"),
            "amount": amount,
            "raw": result,
        }
        lines = [
            "# Puja actualizada",
            f"- Jugador: {name}",
            f"- playerTeamId: {player_team_id}",
            f"- Importe anterior: {format_money(user_bid.get('amount'))}",
            f"- Importe nuevo: {format_money(amount)}",
            f"- Política: {policy}",
        ]
        if offer_id:
            lines.append(f"- offerId: {offer_id}")
        return emit(payload, params.response_format, "\n".join(lines))
    except Exception as exc:
        return _error(exc)


async def laliga_trim_sole_bids(params: TrimSoleBidsInput) -> str:
    """Baja al mínimo las pujas en las que el usuario es el único postor.

    Si ``numberOfBids`` es 1 y la puja es estrictamente mayor que el precio de
    mercado + 1, la deja en ese importe. Si hay más pujas, o el usuario no ha
    pujado, no modifica nada. ``dry_run`` solo informa.

    Args:
        params (TrimSoleBidsInput): dry_run opcional.

    Returns:
        str: Informe markdown o JSON de ajustes, omisiones y errores.
    """
    try:
        policy = get_bid_policy()
    except ValueError as exc:
        return f"Error: {exc}"
    if not params.dry_run:
        blocked = bid_policy_error("update")
        if blocked:
            return blocked
    try:
        client = api()
        ctx = LeagueContext(client)
        league_id = await ctx.league_id(params.league_id)
        planned, without_user_bid = plan_sole_bid_trims(await client.market(league_id))
        adjusted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for row in planned:
            if row.get("action") != "trim":
                skipped.append(row)
                continue
            if params.dry_run:
                adjusted.append({**row, "action": "would_trim"})
                continue
            try:
                raw = await client.update_bid(
                    league_id,
                    str(row["playerTeamId"]),
                    int(row["targetBid"]),
                    offer_id=row.get("offerId"),
                )
                adjusted.append({**row, "action": "trimmed", "raw": raw})
            except Exception as exc:
                errors.append({**row, "action": "error", "reason": _error(exc)})
        payload, markdown = build_trim_report(
            policy=policy,
            dry_run=bool(params.dry_run),
            league_id=league_id,
            adjusted=adjusted,
            skipped=skipped,
            errors=errors,
            without_user_bid=without_user_bid,
        )
        return emit(payload, params.response_format, markdown)
    except Exception as exc:
        return _error(exc)


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

    @mcp.tool(name="laliga_get_real_standings_and_form", annotations=_ann("Clasificación oficial y forma"))
    @flatten(RealStandingsInput)
    async def laliga_get_real_standings_and_form(params: RealStandingsInput) -> str:
        """Obtiene la clasificación oficial de LaLiga EA Sports en tiempo real con estadísticas y racha.

        Incluye posición, puntos, PJ, PG, PE, PP, GF, GC, DG, desglose local/visitante y los últimos 5 partidos.

        Args:
            params (RealStandingsInput): response_format.

        Returns:
            str: Tabla/resumen con clasificación y forma.
        """
        try:
            rows = await ff().real_standings()
            if not rows:
                return "No se pudieron obtener los datos de clasificación oficial."
            lines = [
                "# Clasificación Oficial LaLiga EA Sports y Forma",
                "",
                "Pos | Equipo | Pts | PJ | PG | PE | PP | GF | GC | DG | Casa (Pts/PJ) | Fuera (Pts/PJ) | Racha (últ. 5)",
                "---|---|---|---|---|---|---|---|---|---|---|---|---",
            ]
            for r in rows:
                tot = r.get("total") or {}
                hm = r.get("home") or {}
                aw = r.get("away") or {}
                dg_str = f"+{tot.get('goalDifference')}" if (tot.get("goalDifference") or 0) > 0 else str(tot.get("goalDifference") or 0)
                form_str = r.get("formString") or "n/d"
                lines.append(
                    f"{r['position']} | {r['team']} | **{tot.get('points', 0)}** | {tot.get('played', 0)} | "
                    f"{tot.get('won', 0)} | {tot.get('drawn', 0)} | {tot.get('lost', 0)} | "
                    f"{tot.get('goalsFor', 0)} | {tot.get('goalsAgainst', 0)} | {dg_str} | "
                    f"{hm.get('points', 0)}p ({hm.get('played', 0)}PJ) | {aw.get('points', 0)}p ({aw.get('played', 0)}PJ) | {form_str}"
                )
            return emit({"standings": rows}, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_set_piece_takers", annotations=_ann("Lanzadores a balón parado"))
    @flatten(SetPieceTakersInput)
    async def laliga_get_set_piece_takers(params: SetPieceTakersInput) -> str:
        """Lista la jerarquía de lanzadores a balón parado (1º, 2º y 3º) por equipo de LaLiga.

        Incluye penaltis, faltas directas, faltas indirectas/colgadas y saques de esquina.

        Args:
            params (SetPieceTakersInput): team (slug o nombre del equipo) y response_format.

        Returns:
            str: Especialistas y lanzadores por club.
        """
        try:
            slugs: list[str] | None = None
            if params.team:
                slug = _team_slug(params.team)
                if not slug:
                    return f"No reconozco el equipo '{params.team}'. Usa el slug o nombre del equipo."
                if not is_current_laliga_slug(slug):
                    return _team_not_in_season_message(slug)
                slugs = [slug]
            else:
                slugs = await _slugs_from_calendar()

            teams_data = await ff().all_set_pieces(slugs)
            lines = ["# Especialistas a Balón Parado (LaLiga)", ""]
            for item in teams_data:
                team_name = item.get("fullName") or item.get("team") or item.get("slug")
                if item.get("error"):
                    lines.append(f"## {team_name}\n- Error: {item['error']}\n")
                    continue
                lines.append(f"## {team_name}")
                p = ", ".join(item.get("penalties") or []) or "Sin especialistas destacados"
                fd = ", ".join(item.get("directFouls") or []) or "Sin especialistas destacados"
                fc = ", ".join(item.get("indirectFouls") or []) or "Sin especialistas destacados"
                c = ", ".join(item.get("corners") or []) or "Sin especialistas destacados"
                lines.append(f"- **Penaltis:** {p}")
                lines.append(f"- **Faltas Directas:** {fd}")
                lines.append(f"- **Faltas Indirectas / Colgadas:** {fc}")
                lines.append(f"- **Córners:** {c}")
                lines.append("")
            return emit({"teams": teams_data}, params.response_format, "\n".join(lines).strip())
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_sanctions_and_cards", annotations=_ann("Sanciones y apercibidos"))
    @flatten(SanctionsInput)
    async def laliga_get_sanctions_and_cards(params: SanctionsInput) -> str:
        """Lista jugadores sancionados federativamente (rojas, acumulación) y apercibidos (4 amarillas).

        Args:
            params (SanctionsInput): team opcional y response_format.

        Returns:
            str: Jugadores sancionados y apercibidos para la próxima jornada.
        """
        try:
            data = await ff().sanctions_and_cards()
            sanctioned = data.get("sanctioned") or []
            warned = data.get("warned") or []

            if params.team:
                q = fold(params.team)
                sanctioned = [s for s in sanctioned if q in fold(str(s.get("team") or "")) or q in fold(str(s.get("name") or ""))]
                warned = [w for w in warned if q in fold(str(w.get("team") or "")) or q in fold(str(w.get("name") or ""))]

            lines = ["# Sanciones y Tarjetas (LaLiga)", "", "## Jugadores Sancionados"]
            if not sanctioned:
                lines.append("- No hay jugadores sancionados registrados actualmente.")
            for s in sanctioned:
                tm = f" ({s['team']})" if s.get("team") else ""
                lines.append(f"- **{s['name']}**{tm}: {s.get('reason', 'Sancionado')}")

            lines.extend(["", "## Jugadores Apercibidos (4 amarillas)"])
            if not warned:
                lines.append("- No hay jugadores apercibidos registrados actualmente.")
            for w in warned:
                tm = f" ({w['team']})" if w.get("team") else ""
                lines.append(f"- **{w['name']}**{tm}: {w.get('reason', 'Apercibido')}")

            payload = {
                "sanctioned": sanctioned,
                "warned": warned,
                "totalSanctioned": len(sanctioned),
                "totalWarned": len(warned),
            }
            return emit(payload, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_get_squad_aggregate_stats", annotations=_ann("Estadísticas agregadas de plantilla"))
    @flatten(SquadAggregateStatsInput)
    async def laliga_get_squad_aggregate_stats(params: SquadAggregateStatsInput) -> str:
        """Devuelve en una sola llamada un resumen estadístico agregado de todos los jugadores de la plantilla del usuario o un rival.

        Calcula puntos totales, media de puntos en casa vs fuera, titularidades/suplencias, minutos promediados y tendencia de valor de los últimos 7 días.

        Args:
            params (SquadAggregateStatsInput): team_or_manager ('me' o rival) y response_format.

        Returns:
            str: Resumen estadístico integral por jugador.
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

            teams_master = await ctx.teams_master()
            team_data = await client.team(league_id, tid)
            players_raw = _players_from_team(team_data)
            if not players_raw:
                return f"No se encontraron jugadores en la plantilla de {label}."

            async def _analyze_player(p_item: dict[str, Any]) -> dict[str, Any]:
                pm = p_item.get("playerMaster") if isinstance(p_item.get("playerMaster"), dict) else p_item
                pid = str(pm.get("id") or p_item.get("id") or "")
                name = pm.get("name") or p_item.get("name") or "Jugador"
                pos_id = position_id(pm.get("positionId") or p_item.get("positionId"))
                pos_name = POSITION_NAMES.get(pos_id or 0, "?")
                mv = as_int(p_item.get("marketValue") or pm.get("marketValue"))
                pts = as_int(pm.get("points") or p_item.get("points"))
                avg_pts = float(pm.get("averagePoints") or 0.0)

                # Enrich with player details and market value history
                det_coro = client.player_details(pid, league_id) if pid else asyncio.sleep(0, result={})
                mv_coro = client.player_market_value(pid) if pid else asyncio.sleep(0, result=[])
                det, mv_hist = await asyncio.gather(det_coro, mv_coro, return_exceptions=True)

                det_dict = det if isinstance(det, dict) else {}
                pm_det = det_dict.get("playerMaster") or {}
                last_stats = pm_det.get("lastStats") or pm.get("lastStats") or []

                # Analyze matches home vs away, minutes, starts
                home_pts: list[int] = []
                away_pts: list[int] = []
                starts = 0
                sub_appearances = 0
                total_mins = 0
                games_played = 0

                if isinstance(last_stats, list):
                    for st_item in last_stats:
                        if not isinstance(st_item, dict):
                            continue
                        pts_w = as_int(st_item.get("totalPoints"))
                        loc_vis = st_item.get("locvis") or st_item.get("homeAway")
                        is_home = (loc_vis == "home" or loc_vis == "local" or loc_vis == 1) if loc_vis is not None else None
                        
                        st_dict = st_item.get("stats") or {}
                        mins_pair = st_dict.get("mins_played") or [0, 0]
                        mins = mins_pair[0] if isinstance(mins_pair, list) and mins_pair else as_int(mins_pair)
                        
                        if mins > 0 or pts_w != 0:
                            games_played += 1
                            total_mins += mins
                            if mins >= 60 or st_item.get("isStarter") is True:
                                starts += 1
                            else:
                                sub_appearances += 1
                            if is_home is True:
                                home_pts.append(pts_w)
                            elif is_home is False:
                                away_pts.append(pts_w)
                            else:
                                # default split if unknown
                                home_pts.append(pts_w)

                avg_home = round(sum(home_pts) / len(home_pts), 2) if home_pts else 0.0
                avg_away = round(sum(away_pts) / len(away_pts), 2) if away_pts else 0.0
                avg_mins = round(total_mins / games_played, 1) if games_played > 0 else 0.0

                # 7-day market value trend
                diff_7d = 0
                if isinstance(mv_hist, list) and len(mv_hist) >= 7:
                    v_now = as_int(mv_hist[-1].get("marketValue"))
                    v_old = as_int(mv_hist[-7].get("marketValue"))
                    diff_7d = v_now - v_old
                elif isinstance(mv_hist, list) and len(mv_hist) >= 2:
                    diff_7d = as_int(mv_hist[-1].get("marketValue")) - as_int(mv_hist[0].get("marketValue"))

                return {
                    "id": pid,
                    "name": name,
                    "position": pos_name,
                    "positionId": pos_id,
                    "marketValue": mv,
                    "valueTrend7d": diff_7d,
                    "totalPoints": pts,
                    "averagePoints": avg_pts,
                    "avgHomePoints": avg_home,
                    "avgAwayPoints": avg_away,
                    "gamesPlayed": games_played,
                    "starts": starts,
                    "substitutions": sub_appearances,
                    "averageMinutes": avg_mins,
                    "status": pick_player_status(p_item),
                }

            squad_stats = await asyncio.gather(*[_analyze_player(p) for p in players_raw if isinstance(p, dict)])
            squad_stats.sort(key=lambda x: x["totalPoints"], reverse=True)

            lines = [
                f"# Estadísticas Agregadas de Plantilla — {label}",
                "",
                "Jugador | Pos | Pts | Media | Casa / Fuera | Tit / Sup | Min/P | Valor | Tendencia 7d | Estado",
                "---|---|---|---|---|---|---|---|---|---",
            ]
            for s in squad_stats:
                sign = "+" if s["valueTrend7d"] > 0 else ""
                trend_txt = f"{sign}{format_money(s['valueTrend7d'])}" if s["valueTrend7d"] != 0 else "="
                lines.append(
                    f"{s['name']} | {s['position']} | **{s['totalPoints']}** | {s['averagePoints']} | "
                    f"{s['avgHomePoints']} / {s['avgAwayPoints']} | {s['starts']}T / {s['substitutions']}S | "
                    f"{s['averageMinutes']}' | {format_money(s['marketValue'])} | {trend_txt} | {s['status']}"
                )

            payload = {
                "manager": label,
                "teamId": tid,
                "leagueId": league_id,
                "playerCount": len(squad_stats),
                "players": squad_stats,
            }
            return emit(payload, params.response_format, "\n".join(lines))
        except Exception as exc:
            return _error(exc)

    @mcp.tool(name="laliga_set_lineup", annotations=_ann_write("Modificar alineación"))
    @flatten(SetLineupInput)
    async def laliga_set_lineup(params: SetLineupInput) -> str:
        """Modifica la alineación activa del usuario (formación táctica, 11 titulares, capitán y suplentes).

        Valida la formación legal, resuelve los playerTeamId y hace PUT del XI
        con el contrato plano de la app (tactical_formation + IDs, sin wrapper formation).

        Args:
            params (SetLineupInput): formation ('3-5-2', '4-4-2'), starters (11 IDs/nombres), captain_id y bench opcionales.

        Returns:
            str: Resultado de la operación de alineación.
        """
        try:
            client = api()
            ctx = LeagueContext(client)
            league_id = await ctx.league_id(params.league_id)
            team_id = await ctx.my_team_id(league_id)
            team_data = await client.team(league_id, team_id)
            roster = _players_from_team(team_data)
            payload, resolved_starters, error = prepare_set_lineup_payload(
                roster,
                params.formation,
                list(params.starters),
                captain_id=params.captain_id,
                bench=params.bench,
            )
            if error or payload is None:
                return error or "Error: no se pudo construir la alineación."
            try:
                res = await client.set_lineup(team_id, payload)
            except FantasyAPIError as exc:
                if exc.status_code == 500:
                    return (
                        "Error: LaLiga Fantasy rechazó guardar la alineación (500). "
                        "El endpoint PUT /teams/{id}/lineup sigue activo; el servidor "
                        "falla si el body replica el JSON de GET (wrapper formation / "
                        "tacticalFormation). Se envió el contrato plano de la app "
                        f"(tactical_formation + IDs). {_error(exc)}"
                    )
                return _error(exc)

            lines = [
                "# Alineación Guardada Exitosamente",
                f"- Formación: {params.formation}",
                f"- Titulares ({len(resolved_starters)}): {', '.join(x['name'] for x in resolved_starters)}",
            ]
            if payload.get("captain"):
                lines.append(f"- Capitán: {payload['captain']}")
            return emit(
                {
                    "status": "success",
                    "formation": params.formation,
                    "starters": [item["name"] for item in resolved_starters],
                    "payload": {key: value for key, value in payload.items() if key not in LINEUP_PUT_EXTRAS or value},
                    "raw": res,
                },
                params.response_format,
                "\n".join(lines),
            )
        except Exception as exc:
            return _error(exc)

    mcp.tool(name="laliga_place_bid", annotations=_ann_write("Colocar puja"))(
        flatten(PlaceBidInput)(laliga_place_bid)
    )
    mcp.tool(name="laliga_update_bid", annotations=_ann_write("Actualizar puja"))(
        flatten(UpdateBidInput)(laliga_update_bid)
    )
    mcp.tool(name="laliga_trim_sole_bids", annotations=_ann_write("Recortar pujas únicas"))(
        flatten(TrimSoleBidsInput)(laliga_trim_sole_bids)
    )


def re_split_match(text: str) -> list[str]:
    for sep in (" vs ", " VS ", " v ", " - "):
        if sep in text:
            return text.split(sep, 1)
    return [text]
