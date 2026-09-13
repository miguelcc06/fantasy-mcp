from laliga_fantasy_mcp.services.helpers import lineup_week_points, season_points
from laliga_fantasy_mcp.services.player_stats import summarize_player
from laliga_fantasy_mcp.tools import _attach_season_to_lineup, _summarize_lineup


def test_catalog_without_points_is_none() -> None:
    player = {"id": "3100", "name": "Pedri", "nickname": "Pedri"}
    assert season_points(player) is None
    assert summarize_player(player)["points"] is None
    assert summarize_player(player)["seasonPoints"] is None


def test_catalog_zero_points_is_zero() -> None:
    player = {"id": "1", "name": "Musso", "points": 0}
    assert season_points(player) == 0


def test_lineup_item_uses_week_points_not_season() -> None:
    item = {
        "id": "lineup-1",
        "points": 12,
        "playerMaster": {"id": "1197", "name": "Kubo", "nickname": "Kubo"},
    }
    assert season_points(item) is None
    assert lineup_week_points(item) == 12


def test_summarize_lineup_exposes_week_points() -> None:
    payload = {
        "points": 44,
        "formation": [4, 4, 2],
        "players": [
            {
                "id": "x",
                "points": 12,
                "positionId": 3,
                "playerMaster": {"id": "1197", "name": "Kubo", "nickname": "Kubo", "positionId": 3},
            }
        ],
    }
    info = _summarize_lineup(payload)
    assert info["weekPoints"] == 44
    assert info["starters"][0]["weekPoints"] == 12
    assert "points" not in info["starters"][0]


def test_attach_season_from_roster() -> None:
    lineup = {
        "starters": [{"id": "1197", "name": "Kubo", "weekPoints": 12}],
        "weekPoints": 44,
    }
    roster = [{"id": "1197", "name": "Kubo", "points": 0, "seasonPoints": 0}]
    _attach_season_to_lineup(lineup, roster)
    assert lineup["starters"][0]["seasonPoints"] == 0
    assert lineup["starters"][0]["weekPoints"] == 12
