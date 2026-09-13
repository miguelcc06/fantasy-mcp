from laliga_fantasy_mcp.services.formatters import (
    compact_week_stats,
    format_balances_markdown,
    format_offers_markdown,
    format_week_stats_markdown,
)

def test_empty_offers_are_plain_language() -> None:
    text = format_offers_markdown("Agoumé", "miguel_cc0", [])
    assert "Sin ofertas pendientes." in text
    assert "```" not in text
    assert "[]" not in text


def test_match_stats_drop_coaches_and_zero_minute_players() -> None:
    stats = [
        {
            "date": "2026-09-12",
            "local": {
                "name": "Real Madrid",
                "players": [
                    {"name": "Mbappé", "positionId": 4, "weekPoints": 21, "minutes": 90},
                    {"name": "Mourinho", "positionId": 5, "weekPoints": 5},
                    {"name": "Lunin", "positionId": 1, "weekPoints": 0, "minutes": 0},
                ],
            },
            "visitor": {"name": "Rayo", "players": []},
        }
    ]
    summary = compact_week_stats(5, stats)
    names = [player["name"] for player in summary["matches"][0]["local"]["players"]]
    assert "Mbappé" in names
    assert "Mourinho" not in names
    markdown = format_week_stats_markdown(summary)
    assert "Mbappé" in markdown
    assert "Mourinho" not in markdown
    assert "Lunin" not in markdown


def test_balances_markdown_explains_base_and_reward_ceiling() -> None:
    text = format_balances_markdown(
        {
            "startingBudget": 100_000_000,
            "activityEvents": 34,
            "balances": [
                {
                    "managerName": "David Corral Allo",
                    "balanceBase": 78_605_464,
                    "balance": 78_605_464,
                    "maxDailyRewards": 500_000,
                    "dailyRewardDays": 5,
                    "balanceMax": 79_105_464,
                    "teamValue": 127_806_553,
                    "clauseCost": 6_500_000,
                },
                {
                    "managerName": "miguel_cc0",
                    "balanceBase": 61_063_880,
                    "balance": 61_063_880,
                    "maxDailyRewards": 500_000,
                    "dailyRewardDays": 5,
                    "balanceMax": 61_563_880,
                    "teamValue": 150_226_496,
                    "officialCash": 61_063_880,
                },
            ],
        }
    )
    assert "no se puede saber si la cobraron" in text
    assert "Saldo base: 78.605.464 €" in text
    assert "Recompensas máximas posibles: 500.000 € (5 días)" in text
    assert "Techo (base + recompensas): 79.105.464 €" in text
    assert "no ha reclamado recompensas" in text
