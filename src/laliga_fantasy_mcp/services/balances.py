"""Cálculo de saldos estimados a partir de actividad, cláusulas y mercado."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from laliga_fantasy_mcp.client.api import FantasyAPIError, FantasyClient
from laliga_fantasy_mcp.config import (
    CLAUSE_INCREASE_PAY_RATIO,
    CLAUSE_MIN,
    CLAUSE_RATIO_DEN,
    CLAUSE_RATIO_NUM,
    DAILY_REWARD_FREE,
    OWNERSHIP_CONCURRENCY,
    STARTING_BUDGET,
)
from laliga_fantasy_mcp.services.helpers import (
    as_int,
    as_money,
    manager_id_from,
    manager_name,
    nested_player,
    player_id,
    player_name,
    team_id_from,
)
from laliga_fantasy_mcp.services.ownership import _players_from_team

EXPENSE_TYPES = {1, 31, 32}
INCOME_TYPES = {6, 33}
# 4 = blindaje: gasto si hay amount


def default_initial_clause(join_market_value: int) -> int:
    if join_market_value < CLAUSE_MIN:
        return CLAUSE_MIN
    return join_market_value * CLAUSE_RATIO_NUM // CLAUSE_RATIO_DEN


def paid_clause_raise(clause: int, market_value: int, baseline: int) -> int:
    """Incremento de cláusula que el mánager pagó (no el seguimiento automático del VM)."""
    floor = CLAUSE_MIN if market_value < CLAUSE_MIN else 0
    return max(0, clause - max(baseline, market_value, floor))


def clause_increase_cost(raise_amount: int) -> int:
    return raise_amount // CLAUSE_INCREASE_PAY_RATIO


def _manager_id(item: dict[str, Any], entry: dict[str, Any] | None = None) -> str:
    mid = manager_id_from(item)
    if mid:
        return mid
    if entry:
        mid = manager_id_from(entry)
        if mid:
            return mid
        tid = team_id_from(entry)
        if tid:
            return tid
    return "unknown"


def _seller_id(item: dict[str, Any]) -> str | None:
    if item.get("user2Id") is not None:
        return str(item["user2Id"])
    for key in ("seller", "fromTeam", "previousTeam", "originTeam"):
        value = item.get(key)
        if isinstance(value, dict):
            manager = value.get("manager") if isinstance(value.get("manager"), dict) else {}
            raw = manager.get("id") or value.get("id") or value.get("teamId")
            if raw is not None:
                return str(raw)
        elif value:
            return str(value)
    return None


def purchase_prices_from_activity(events: list[dict[str, Any]]) -> dict[str, int]:
    prices: dict[str, int] = {}
    for item in events:
        if not isinstance(item, dict):
            continue
        if as_int(item.get("activityTypeId") or item.get("type")) not in EXPENSE_TYPES:
            continue
        pid = str(item.get("playerMasterId") or player_id(item) or "")
        amount = abs(as_int(item.get("amount") or item.get("money") or item.get("price")))
        if pid and amount:
            prices[pid] = amount
    return prices


def join_dates_from_activity(events: list[dict[str, Any]]) -> dict[str, str]:
    dates: dict[str, str] = {}
    for item in events:
        if not isinstance(item, dict):
            continue
        if as_int(item.get("activityTypeId")) != 9:
            continue
        mid = str(item.get("user1Id") or "")
        raw = str(item.get("createdAt") or "")
        if mid and len(raw) >= 10:
            dates[mid] = raw[:10]
    return dates


def market_value_on_day(history: list[Any], day: str) -> int | None:
    for row in history:
        if isinstance(row, dict) and str(row.get("date") or "").startswith(day):
            return as_int(row.get("marketValue"))
    return None


def daily_reward_days(joined_on: str | None, as_of: date | None = None) -> int:
    if not joined_on:
        return 0
    try:
        start = date.fromisoformat(joined_on[:10])
    except ValueError:
        return 0
    end = as_of or date.today()
    days = (end - start).days + 1
    return days if days > 0 else 0


def daily_reward_ceiling(joined_on: str | None, as_of: date | None = None) -> int:
    return daily_reward_days(joined_on, as_of) * DAILY_REWARD_FREE


async def load_activity(client: FantasyClient, league_id: str, max_pages: int = 40) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for page in range(max_pages):
        try:
            chunk = await client.activity(league_id, page)
        except FantasyAPIError:
            break
        if not chunk:
            break
        events.extend(item for item in chunk if isinstance(item, dict))
        if len(chunk) < 5:
            break
    return events


async def load_clause_snapshots(
    client: FantasyClient,
    league_id: str,
    standings: list[Any],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Plantillas actuales + VM del día de alta para detectar subidas de cláusula pagadas."""
    purchases = purchase_prices_from_activity(events)
    join_dates = join_dates_from_activity(events)
    semaphore = asyncio.Semaphore(OWNERSHIP_CONCURRENCY)
    snapshots: list[dict[str, Any]] = []

    async def load_team(entry: dict[str, Any]) -> None:
        tid = team_id_from(entry)
        if not tid:
            return
        async with semaphore:
            try:
                team = await client.team(league_id, tid)
            except FantasyAPIError:
                return
        mid = manager_id_from(entry) or str((team.get("manager") or {}).get("id") or "")
        for player in _players_from_team(team):
            if not isinstance(player, dict):
                continue
            master = nested_player(player)
            pid = player_id(player) or (str(master.get("id")) if master.get("id") is not None else None)
            if not pid:
                continue
            snapshots.append(
                {
                    "teamId": tid,
                    "managerId": mid,
                    "playerId": pid,
                    "player": player_name(player),
                    "buyoutClause": as_int(player.get("buyoutClause") or player.get("clause")),
                    "marketValue": as_int(master.get("marketValue") or player.get("marketValue")),
                    "purchasePrice": purchases.get(pid),
                    "joinMarketValue": None,
                    "joinDay": join_dates.get(mid),
                }
            )

    await asyncio.gather(*(load_team(entry) for entry in standings if isinstance(entry, dict)))

    needed = {row["playerId"] for row in snapshots if row["purchasePrice"] is None and row.get("joinDay")}
    histories: dict[str, list[Any]] = {}

    async def load_history(pid: str) -> None:
        async with semaphore:
            try:
                payload = await client.player_market_value(pid)
            except FantasyAPIError:
                payload = []
        histories[pid] = payload if isinstance(payload, list) else []

    await asyncio.gather(*(load_history(pid) for pid in needed))
    for row in snapshots:
        if row["purchasePrice"] is not None or not row.get("joinDay"):
            continue
        row["joinMarketValue"] = market_value_on_day(histories.get(row["playerId"]) or [], row["joinDay"])
        row.pop("joinDay", None)
    return snapshots


def compute_balances(
    standings: list[Any],
    events: list[dict[str, Any]],
    history: list[Any] | None = None,
    clause_snapshots: list[dict[str, Any]] | None = None,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    ledger: dict[str, dict[str, Any]] = {}
    team_to_manager: dict[str, str] = {}
    join_dates = join_dates_from_activity(events)
    for entry in standings:
        if not isinstance(entry, dict):
            continue
        mid = manager_id_from(entry) or team_id_from(entry)
        if not mid:
            continue
        tid = team_id_from(entry)
        if tid:
            team_to_manager[tid] = mid
        ledger[mid] = {
            "managerId": mid,
            "managerName": manager_name(entry),
            "teamId": tid,
            "teamValue": as_int(entry.get("teamValue") or (entry.get("team") or {}).get("teamValue")),
            "points": as_int(entry.get("points") or entry.get("livePoints")),
            "balance": STARTING_BUDGET,
            "movements": 0,
            "ledger": [],
            "clauseCost": 0,
            "clauseRaises": [],
            "dailyRewardDays": daily_reward_days(join_dates.get(mid), as_of),
            "maxDailyRewards": daily_reward_ceiling(join_dates.get(mid), as_of),
            "dailyRewardCeiling": daily_reward_ceiling(join_dates.get(mid), as_of),
        }

    def apply_delta(
        manager_key: str | None,
        amount: int,
        name_hint: str = "",
        *,
        reason: str = "",
        player: str | None = None,
    ) -> None:
        if not manager_key or manager_key == "unknown":
            return
        if manager_key not in ledger:
            ledger[manager_key] = {
                "managerId": manager_key,
                "managerName": name_hint or manager_key,
                "teamId": None,
                "teamValue": 0,
                "points": 0,
                "balance": STARTING_BUDGET,
                "movements": 0,
                "ledger": [],
                "clauseCost": 0,
                "clauseRaises": [],
                "dailyRewardDays": 0,
                "maxDailyRewards": 0,
                "dailyRewardCeiling": 0,
            }
        ledger[manager_key]["balance"] += amount
        ledger[manager_key]["movements"] += 1
        ledger[manager_key]["ledger"].append(
            {
                "reason": reason,
                "player": player,
                "amount": amount,
            }
        )

    for item in events:
        activity_type = as_int(item.get("activityTypeId") or item.get("type"))
        amount = abs(as_int(item.get("amount") or item.get("money") or item.get("price")))
        actor = _manager_id(item)
        seller = _seller_id(item)
        pname = player_name(item) or str(item.get("playerMasterId") or "") or None
        if activity_type in EXPENSE_TYPES and amount:
            apply_delta(actor, -amount, manager_name(item), reason="purchase", player=pname)
            if seller and seller != actor:
                apply_delta(seller, amount, reason="sale_p2p", player=pname)
        elif activity_type in INCOME_TYPES and amount:
            apply_delta(actor, amount, manager_name(item), reason="sale", player=pname)
        elif activity_type == 4 and amount:
            apply_delta(actor, -amount, manager_name(item), reason="shield", player=pname)
        elif amount and not activity_type:
            if not item.get("playerMasterId") and not item.get("playerName"):
                apply_delta(actor, amount, manager_name(item), reason="bonus")

    # /market/history suele ser solo del usuario autenticado y sin managerId.
    # No se aplica: duplicaría compras propias si un día viniera con IDs.
    _ = history

    for snap in clause_snapshots or []:
        if not isinstance(snap, dict):
            continue
        mid = str(snap.get("managerId") or "") or team_to_manager.get(str(snap.get("teamId") or ""), "")
        if mid not in ledger:
            continue
        clause = as_int(snap.get("buyoutClause"))
        mv = as_int(snap.get("marketValue"))
        purchase = snap.get("purchasePrice")
        if purchase is not None:
            baseline = as_int(purchase)
        elif snap.get("joinMarketValue") is not None:
            baseline = default_initial_clause(as_int(snap.get("joinMarketValue")))
        else:
            continue
        raise_amt = paid_clause_raise(clause, mv, baseline)
        if not raise_amt:
            continue
        cost = clause_increase_cost(raise_amt)
        if not cost:
            continue
        apply_delta(
            mid,
            -cost,
            reason="clause_increase",
            player=str(snap.get("player") or snap.get("playerId") or ""),
        )
        ledger[mid]["clauseCost"] += cost
        ledger[mid]["clauseRaises"].append(
            {
                "player": snap.get("player"),
                "playerId": snap.get("playerId"),
                "raise": raise_amt,
                "cost": cost,
            }
        )

    rows = list(ledger.values())
    for row in rows:
        row["balanceBase"] = row["balance"]
        row["balanceBeforeClauses"] = row["balance"] + row["clauseCost"]
        row["maxDailyRewards"] = row.get("maxDailyRewards") or row.get("dailyRewardCeiling") or 0
        row["dailyRewardCeiling"] = row["maxDailyRewards"]
        row["balanceMax"] = row["balanceBase"] + row["maxDailyRewards"]
        row["asOf"] = (as_of or date.today()).isoformat()
    rows.sort(key=lambda row: row["balanceBase"], reverse=True)
    return rows
