"""Política de pujas y recorte de pujas únicas del usuario."""

from __future__ import annotations

from typing import Any

from laliga_fantasy_mcp.config import get_bid_policy
from laliga_fantasy_mcp.services.formatters import format_money
from laliga_fantasy_mcp.services.helpers import (
    as_int,
    as_money,
    fuzzy_match,
    market_player_team_id,
    player_id,
    player_name,
)


def bid_policy_error(action: str) -> str | None:
    """None si la política permite ``place`` o ``update``. Si no, texto de error."""
    try:
        policy = get_bid_policy()
    except ValueError as exc:
        return f"Error: {exc}"
    if policy == "create_and_update":
        return None
    if policy == "readonly":
        verb = "colocar" if action == "place" else "actualizar"
        return (
            "Error: LALIGA_FANTASY_BID_POLICY=readonly impide "
            f"{verb} pujas. Colocar y actualizar pujas está prohibido. "
            "Valores permitidos: readonly, update_own (solo actualizar pujas propias) "
            "o create_and_update (crear y actualizar)."
        )
    if policy == "update_own" and action == "place":
        return (
            "Error: LALIGA_FANTASY_BID_POLICY=update_own impide crear pujas nuevas. "
            "Solo se pueden actualizar pujas que ya hayas hecho. "
            "Usa laliga_update_bid o define create_and_update para colocar pujas."
        )
    if policy == "update_own" and action == "update":
        return None
    return f"Error: acción de puja no reconocida ({action})."


def extract_user_bid(item: dict[str, Any]) -> dict[str, Any] | None:
    """Puja del usuario en un ítem de mercado, o None si no tiene puja activa."""
    bid = item.get("bid")
    if bid is None or bid is False:
        return None
    if isinstance(bid, bool):
        return None
    if isinstance(bid, (int, float)):
        amount = as_int(bid)
        if amount <= 0:
            return None
        return {"amount": amount, "offerId": None}
    if isinstance(bid, str):
        amount = as_int(bid) if bid.strip().isdigit() else 0
        if amount <= 0:
            return None
        return {"amount": amount, "offerId": None}
    if not isinstance(bid, dict):
        return None
    raw_amount = None
    for key in ("money", "offer", "amount", "value"):
        if bid.get(key) not in (None, ""):
            raw_amount = bid.get(key)
            break
    amount = as_money(raw_amount) if raw_amount is not None else 0
    raw_id = bid.get("id")
    if raw_id in (None, ""):
        raw_id = bid.get("offerId") if bid.get("offerId") not in (None, "") else bid.get("offer_id")
    offer_id = str(raw_id) if raw_id not in (None, "") else None
    if amount <= 0:
        return None
    return {"amount": amount, "offerId": offer_id}


def listing_market_price(item: dict[str, Any]) -> int:
    """Precio de referencia: venta, precio de mercado o valor de mercado, el primero > 0."""
    master = item.get("playerMaster") if isinstance(item.get("playerMaster"), dict) else {}
    for candidate in (
        item.get("salePrice"),
        item.get("price"),
        master.get("marketValue"),
        item.get("marketValue"),
    ):
        if candidate is None or candidate == "":
            continue
        amount = as_money(candidate)
        if amount > 0:
            return amount
    return 0


def listing_bid_count(item: dict[str, Any]) -> int | None:
    if "numberOfBids" not in item or item.get("numberOfBids") is None:
        return None
    return as_int(item.get("numberOfBids"))


def find_market_listings(items: list[Any], query: str) -> list[dict[str, Any]]:
    """Coincidencias exactas de id primero; el nombre solo si la query no es numérica."""
    needle = query.strip()
    exact: list[dict[str, Any]] = []
    fuzzy: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = player_id(item) or ""
        ptid = market_player_team_id(item) or ""
        mid = str(item["id"]) if item.get("id") is not None else ""
        if needle and needle in {pid, ptid, mid}:
            exact.append(item)
            continue
        if needle.isdigit():
            continue
        if fuzzy_match(needle, player_name(item)):
            fuzzy.append(item)
    return exact or fuzzy


def resolve_bid_player(
    items: list[Any],
    query: str,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Devuelve (listing, player_team_id, error).

    Un id numérico que no está en el mercado se acepta como playerTeamId directo
    (solo tiene sentido al crear; actualizar exige ver la puja en el mercado).
    """
    matches = find_market_listings(items, query)
    if len(matches) > 1:
        names = ", ".join(player_name(item) or "?" for item in matches[:8])
        return (
            None,
            None,
            f"Error: '{query}' coincide con varios jugadores del mercado ({names}). "
            "Afina el nombre o pasa el playerTeamId.",
        )
    if len(matches) == 1:
        listing = matches[0]
        return listing, market_player_team_id(listing), None
    stripped = query.strip()
    if stripped.isdigit():
        return None, stripped, None
    return (
        None,
        None,
        f"Error: '{query}' no está en el mercado. "
        "Pasa el playerTeamId o el nombre del jugador en venta o libre.",
    )


def missing_player_team_id_error(listing: dict[str, Any] | None, player_team_id: str | None, query: str) -> str | None:
    if player_team_id:
        return None
    label = player_name(listing) if listing else query
    return (
        f"Error: '{label}' no tiene playerTeamId. "
        "La API de ofertas de LaLiga Fantasy lo necesita para crear o actualizar la puja."
    )


def classify_user_bid(item: dict[str, Any]) -> dict[str, Any] | None:
    """Plan de recorte para una ficha en la que el usuario ya ha pujado.

    None si el usuario no tiene puja en ese jugador.
    """
    user_bid = extract_user_bid(item)
    if user_bid is None:
        return None
    bids = listing_bid_count(item)
    price = listing_market_price(item)
    current = int(user_bid["amount"])
    target = price + 1 if price > 0 else None
    row: dict[str, Any] = {
        "player": player_name(item) or "Jugador",
        "playerId": player_id(item),
        "playerTeamId": market_player_team_id(item),
        "offerId": user_bid.get("offerId"),
        "numberOfBids": bids,
        "marketPrice": price,
        "currentBid": current,
        "targetBid": None,
        "action": "skipped",
        "reason": "",
    }
    if not row["playerTeamId"]:
        row["reason"] = "No tiene playerTeamId; no se modifica."
        return row
    if bids is None:
        row["reason"] = "numberOfBids no está disponible; no se modifica."
        return row
    if bids > 1:
        row["reason"] = f"Hay competencia ({bids} pujas); no se modifica."
        return row
    if bids != 1:
        row["reason"] = f"numberOfBids={bids} no indica una puja única; no se modifica."
        return row
    if target is None:
        row["reason"] = "Precio de mercado no disponible; no se modifica."
        return row
    if current <= target:
        row["reason"] = (
            f"La puja ({format_money(current)}) no supera el precio de mercado + 1 "
            f"({format_money(target)}); no se modifica."
        )
        return row
    row["targetBid"] = target
    row["action"] = "trim"
    row["reason"] = (
        f"Puja única por encima del mínimo: {format_money(current)} → {format_money(target)} "
        f"(precio de mercado {format_money(price)})."
    )
    return row


def plan_sole_bid_trims(items: list[Any]) -> tuple[list[dict[str, Any]], int]:
    """Filas con puja del usuario y cuántas fichas del mercado no tienen puja suya."""
    rows: list[dict[str, Any]] = []
    without_user_bid = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        row = classify_user_bid(item)
        if row is None:
            without_user_bid += 1
        else:
            rows.append(row)
    return rows, without_user_bid


def build_trim_report(
    *,
    policy: str,
    dry_run: bool,
    league_id: str,
    adjusted: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    without_user_bid: int,
) -> tuple[dict[str, Any], str]:
    mode = "simulación (no se ha modificado ninguna puja)" if dry_run else "aplicado"
    lines = [
        "# Recorte de pujas únicas",
        "",
        f"- Liga: {league_id}",
        f"- Política: {policy}",
        f"- Modo: {mode}",
        "",
        "## Ajustes",
    ]
    if not adjusted:
        lines.append("- Ninguno.")
    for row in adjusted:
        verb = "se bajaría" if row.get("action") == "would_trim" else "bajada"
        lines.append(
            f"- {row.get('player')}: {format_money(row.get('currentBid'))} → "
            f"{format_money(row.get('targetBid'))} "
            f"({verb}; precio de mercado {format_money(row.get('marketPrice'))}, "
            f"{row.get('numberOfBids')} puja)"
        )
    lines.extend(["", "## Omitidas"])
    if not skipped:
        lines.append("- Ninguna.")
    for row in skipped:
        lines.append(f"- {row.get('player')}: {row.get('reason')}")
    if errors:
        lines.extend(["", "## Errores"])
        for row in errors:
            lines.append(f"- {row.get('player')}: {row.get('reason')}")
    lines.extend(
        [
            "",
            (
                f"Resumen: {len(adjusted)} ajuste(s), {len(skipped)} omitida(s), "
                f"{len(errors)} error(es), {without_user_bid} jugador(es) del mercado sin puja tuya."
            ),
        ]
    )
    payload = {
        "policy": policy,
        "dryRun": dry_run,
        "leagueId": league_id,
        "adjusted": adjusted,
        "skipped": skipped,
        "errors": errors,
        "summary": {
            "adjusted": len(adjusted),
            "skipped": len(skipped),
            "errors": len(errors),
            "marketWithoutUserBid": without_user_bid,
        },
    }
    return payload, "\n".join(lines)
