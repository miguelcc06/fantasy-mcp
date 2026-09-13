from laliga_fantasy_mcp.config import CURRENT_LALIGA_SLUGS, LALIGA_TEAMS
from laliga_fantasy_mcp.services.helpers import is_current_laliga_slug, resolve_team_slug


def test_current_season_has_twenty_clubs() -> None:
    assert len(CURRENT_LALIGA_SLUGS) == 20
    assert len(set(CURRENT_LALIGA_SLUGS)) == 20
    for slug in ("girona", "mallorca", "oviedo"):
        assert slug not in CURRENT_LALIGA_SLUGS
        assert slug in LALIGA_TEAMS
        assert not is_current_laliga_slug(slug)


def test_resolve_team_slug_exact_and_aliases() -> None:
    assert resolve_team_slug("real-madrid") == "real-madrid"
    assert resolve_team_slug("Real Madrid") == "real-madrid"
    assert resolve_team_slug("madrid") == "real-madrid"
    assert resolve_team_slug("Real Betis") == "betis"
    assert resolve_team_slug("C.A. Osasuna") == "osasuna"
    assert resolve_team_slug("R. Racing Club") == "racing"


def test_real_oviedo_does_not_404_via_short_substring() -> None:
    assert resolve_team_slug("oviedo") == "oviedo"
    assert resolve_team_slug("real-oviedo") == "oviedo"
    assert resolve_team_slug("Real Oviedo") == "oviedo"
    assert resolve_team_slug("girona") == "girona"
    assert resolve_team_slug("zzz-no-existe") is None
