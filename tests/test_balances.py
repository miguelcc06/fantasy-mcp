from datetime import date

from laliga_fantasy_mcp.services.balances import (
    clause_increase_cost,
    compute_balances,
    default_initial_clause,
    paid_clause_raise,
)

DAVID = "12279070"
MIGUEL = "12309800"

STANDINGS = [
    {
        "points": 27,
        "team": {
            "id": "40057212",
            "teamValue": 127_806_553,
            "manager": {"id": DAVID, "managerName": "David Corral Allo"},
        },
    },
    {
        "points": 25,
        "team": {
            "id": "40057355",
            "teamValue": 150_226_496,
            "manager": {"id": MIGUEL, "managerName": "miguel_cc0"},
        },
    },
]

EVENTS = [
    {"activityTypeId": 33, "user1Id": DAVID, "playerMasterId": 3117, "amount": 11_663_995},
    {"activityTypeId": 1, "user1Id": MIGUEL, "user2Id": DAVID, "playerMasterId": 1991, "amount": 10_114_708},
    {"activityTypeId": 33, "user1Id": DAVID, "playerMasterId": 3023, "amount": 8_453_902},
    {"activityTypeId": 33, "user1Id": DAVID, "playerMasterId": 2698, "amount": 4_722_859},
    {"activityTypeId": 31, "user1Id": DAVID, "playerMasterId": 2600, "amount": 23_000_000},
    {"activityTypeId": 31, "user1Id": DAVID, "playerMasterId": 2552, "amount": 18_750_000},
    {"activityTypeId": 31, "user1Id": DAVID, "playerMasterId": 2963, "amount": 8_100_000},
    {"activityTypeId": 31, "user1Id": MIGUEL, "playerMasterId": 2454, "amount": 15_000_000},
    {"activityTypeId": 31, "user1Id": MIGUEL, "playerMasterId": 2985, "amount": 23_000_000},
    {"activityTypeId": 33, "user1Id": MIGUEL, "playerMasterId": 3126, "amount": 9_178_588},
    {"activityTypeId": 9, "user1Id": DAVID, "createdAt": "2026-09-09T19:00:55+02:00"},
    {"activityTypeId": 9, "user1Id": MIGUEL, "createdAt": "2026-09-09T19:03:52+02:00"},
]

DAVID_CLAUSES = [
    {
        "teamId": "40057212",
        "managerId": DAVID,
        "playerId": "2962",
        "player": "Luismi Cruz",
        "buyoutClause": 18_737_821,
        "marketValue": 9_006_186,
        "purchasePrice": None,
        "joinMarketValue": 7_642_693,
    },
    {
        "teamId": "40057212",
        "managerId": DAVID,
        "playerId": "2639",
        "player": "M. Román",
        "buyoutClause": 18_817_536,
        "marketValue": 10_916_595,
        "purchasePrice": None,
        "joinMarketValue": 9_490_522,
    },
    {
        "teamId": "40057212",
        "managerId": DAVID,
        "playerId": "2600",
        "player": "Mariano",
        "buyoutClause": 27_000_000,
        "marketValue": 16_075_920,
        "purchasePrice": 23_000_000,
        "joinMarketValue": None,
    },
    {
        "teamId": "40057212",
        "managerId": DAVID,
        "playerId": "2552",
        "player": "Jonny Otto",
        "buyoutClause": 18_750_000,
        "marketValue": 12_576_929,
        "purchasePrice": 18_750_000,
        "joinMarketValue": None,
    },
]


def _row(rows: list[dict], manager_id: str) -> dict:
    return next(row for row in rows if row["managerId"] == manager_id)


def test_default_initial_clause_uses_five_thirds_or_floor() -> None:
    assert default_initial_clause(7_642_693) == 12_737_821
    assert default_initial_clause(394_157) == 1_000_000


def test_paid_raise_ignores_market_follow_and_one_million_floor() -> None:
    assert paid_clause_raise(27_000_000, 16_075_920, 23_000_000) == 4_000_000
    assert paid_clause_raise(37_159_470, 37_159_470, 35_102_138) == 0
    assert paid_clause_raise(1_000_000, 439_614, 435_753) == 0
    assert clause_increase_cost(4_000_000) == 2_000_000
    assert clause_increase_cost(3_000_000) == 1_500_000


def test_p2p_sale_credits_seller_once() -> None:
    rows = compute_balances(STANDINGS, EVENTS, as_of=date(2026, 9, 13))
    david = _row(rows, DAVID)
    assert david["balance"] == 85_105_464
    assert sum(1 for item in david["ledger"] if item["reason"] == "sale_p2p") == 1


def test_own_market_ledger_matches_official_cash() -> None:
    rows = compute_balances(STANDINGS, EVENTS, as_of=date(2026, 9, 13))
    assert _row(rows, MIGUEL)["balance"] == 61_063_880


def test_clause_raises_explain_david_gap_except_daily_rewards() -> None:
    rows = compute_balances(
        STANDINGS,
        EVENTS,
        clause_snapshots=DAVID_CLAUSES,
        as_of=date(2026, 9, 13),
    )
    david = _row(rows, DAVID)
    assert david["clauseCost"] == 6_500_000
    assert david["balance"] == 78_605_464
    assert david["balanceBase"] == 78_605_464
    assert david["dailyRewardDays"] == 5
    assert david["maxDailyRewards"] == 500_000
    assert david["dailyRewardCeiling"] == 500_000
    assert david["balanceMax"] == 79_105_464
    assert {item["player"] for item in david["clauseRaises"]} == {"Luismi Cruz", "M. Román", "Mariano"}
    assert _row(rows, MIGUEL)["balanceBase"] == 61_063_880
