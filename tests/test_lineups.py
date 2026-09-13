from laliga_fantasy_mcp.client.futbolfantasy import parse_lineup_html, parse_percent


HTML = """
<div class="jugadores-titulares">
  <div class="jugador tipo_lista" data-nombre="Courtois" data-probabilidad="70%">
    <span class="nombre">Courtois</span>
  </div>
  <div class="jugador tipo_lista" data-nombre="Mbappe" data-probabilidad="95%">
    <span class="nombre">Mbappé</span>
  </div>
</div>
<div class="jugadores-suplentes">
  <div class="jugador tipo_lista" data-nombre="Lunin" data-probabilidad="15%">
    <span class="nombre">Lunin</span>
  </div>
</div>
"""

MALLORCA_HTML = """
<div class="jugadores-titulares">
  <div class="jugador tipo_lista">
    <span class="nombre">1.73</span>
    70%
    <a href="/jugadores/vedat-muriqi">Muriqi</a>
  </div>
  <div class="jugador tipo_lista">
    <span class="truncate-name">1.94</span>
    55%
    <a href="/jugadores/pablo-maffeo"></a>
  </div>
</div>
"""

CAMISETA_HTML = """
<div class="camiseta-wrapper" data-onceff="titular">
  <a href="/jugadores/kylian-mbappe">
    <span class="truncate-name">1.82 70%</span>
  </a>
</div>
<div class="camiseta-wrapper" data-onceff="suplente">
  <a href="/jugadores/andriy-lunin">
    <span class="truncate-name">1.91 12%</span>
  </a>
</div>
"""


def test_parse_percent_strips_suffix() -> None:
    assert parse_percent("70%") == 70
    assert parse_percent("70") == 70
    assert parse_percent(80) == 80
    assert parse_percent("1.82 70%") == 70
    assert parse_percent("1.82") is None
    assert parse_percent(None) is None


def test_lineup_html_keeps_probability_and_bench() -> None:
    parsed = parse_lineup_html(HTML, "real-madrid")
    assert parsed["starters"][0]["probability"] == 70
    assert parsed["starters"][1]["probability"] == 95
    assert parsed["bench"][0]["name"] == "Lunin"
    assert parsed["bench"][0]["probability"] == 15


def test_height_is_not_used_as_player_name() -> None:
    parsed = parse_lineup_html(MALLORCA_HTML, "mallorca")
    names = [player["name"] for player in parsed["starters"]]
    assert names == ["Muriqi", "Pablo Maffeo"]
    assert all(not name[0].isdigit() for name in names)
    assert parsed["starters"][0]["probability"] == 70
    assert parsed["starters"][1]["probability"] == 55


def test_camiseta_fallback_uses_href_and_percent() -> None:
    parsed = parse_lineup_html(CAMISETA_HTML, "real-madrid")
    assert parsed["starters"][0]["name"] == "Kylian Mbappe"
    assert parsed["starters"][0]["probability"] == 70
    assert parsed["bench"][0]["name"] == "Andriy Lunin"
    assert parsed["bench"][0]["probability"] == 12


def test_junk_only_xi_is_empty_with_note() -> None:
    html = """
    <div class="jugadores-titulares">
      <div class="jugador tipo_lista"><span class="nombre">1.73</span></div>
    </div>
    """
    parsed = parse_lineup_html(html, "mallorca")
    assert parsed["starters"] == []
    assert parsed["note"]
