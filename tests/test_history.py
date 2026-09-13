from laliga_fantasy_mcp.services.helpers import merge_market_history


EVENTS = [
    {
        "activityTypeId": 33,
        "playerMasterId": "1",
        "user1Id": "m1",
        "amount": 12_000_000,
        "createdAt": "2026-09-12",
    },
    {
        "activityTypeId": 31,
        "playerMasterId": "2",
        "user1Id": "m2",
        "amount": 23_000_000,
        "createdAt": "2026-09-11",
    },
    {
        "activityTypeId": 6,
        "playerMasterId": "3",
        "user1Id": "m2",
        "amount": 100_000,
    },
]

HISTORY = [
    {
        "playerName": "Noubi",
        "id": "2",
        "operation": "purchase",
        "money": 23_000_000,
    },
    {
        "playerName": "Uche",
        "id": "9",
        "operation": "sale",
        "money": 9_178_588,
    },
]

MANAGERS = {"m1": "Dieg0ArmandoMetadona", "m2": "miguel_cc0"}
PLAYERS = {"1": "Robbie Ure", "2": "Noubi", "9": "Uche"}


def test_merge_keeps_league_activity_and_dedups_history() -> None:
    rows = merge_market_history(HISTORY, EVENTS, MANAGERS, PLAYERS)
    players = [row["player"] for row in rows]
    assert "Robbie Ure" in players
    assert "Noubi" in players
    assert "Uche" in players
    assert players.count("Noubi") == 1
    ure = next(row for row in rows if row["player"] == "Robbie Ure")
    assert ure["operation"] == "sale"
    assert ure["manager"] == "Dieg0ArmandoMetadona"
    assert ure["amount"] == 12_000_000


def test_merge_ignores_non_market_activity() -> None:
    rows = merge_market_history([], EVENTS, MANAGERS, PLAYERS)
    assert all(row["player"] != "3" for row in rows)
    assert len(rows) == 2
