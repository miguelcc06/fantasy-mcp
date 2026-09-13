from laliga_fantasy_mcp.services.helpers import fuzzy_match
from laliga_fantasy_mcp.services.player_stats import find_players


CATALOG = [
    {"id": "2974", "name": "Noé", "nickname": "Noé", "playerStatus": "out_of_league", "points": 0},
    {"id": "3100", "name": "Pedri", "nickname": "Pedri", "playerStatus": "ok", "points": 33},
    {"id": "1", "name": "Kylian Mbappé", "nickname": "Mbappé", "playerStatus": "ok", "points": 40},
]


def test_garbage_query_does_not_match_noe() -> None:
    assert find_players(CATALOG, "xyznoexiste123") == []
    assert not fuzzy_match("xyznoexiste123", "Noé")


def test_exact_short_name_still_matches() -> None:
    matches = find_players(CATALOG, "Noé")
    assert [player["name"] for player in matches] == ["Noé"]


def test_prefix_finds_pedri() -> None:
    matches = find_players(CATALOG, "Ped")
    assert matches and matches[0]["name"] == "Pedri"


def test_out_of_league_not_returned_on_weak_match() -> None:
    assert find_players(CATALOG, "noexiste") == []
