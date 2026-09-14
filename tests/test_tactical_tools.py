import pytest
from laliga_fantasy_mcp.client.futbolfantasy import (
    parse_real_standings_html,
    parse_team_set_pieces,
    parse_sanctions_html,
    parse_apercibidos_html,
)

SAMPLE_STANDINGS_HTML = """
<table class="clasi-comp"><tr><th></th><th>Total</th><th>En casa</th><th>Fuera</th></tr></table>
<table class="clasi-comp">
  <tr><th>Equipo</th></tr>
  <tr><td><span class="posicion">1</span><a href="#">Real Madrid</a><div class="clasi-racha-container"><span class="clasi-racha won">J5</span><span class="clasi-racha draw">J4</span></div></td></tr>
</table>
<table class="clasi-comp">
  <tr><th>Pt</th><th>PJ</th><th>G</th><th>E</th><th>P</th><th>GF</th><th>GC</th><th>DG</th></tr>
  <tr><td>13</td><td>5</td><td>4</td><td>1</td><td>0</td><td>10</td><td>2</td><td>+8</td></tr>
</table>
<table class="clasi-comp">
  <tr><th>Pt</th><th>PJ</th><th>G</th><th>E</th><th>P</th><th>GF</th><th>GC</th><th>DG</th></tr>
  <tr><td>6</td><td>2</td><td>2</td><td>0</td><td>0</td><td>4</td><td>0</td><td>+4</td></tr>
</table>
<table class="clasi-comp">
  <tr><th>Pt</th><th>PJ</th><th>G</th><th>E</th><th>P</th><th>GF</th><th>GC</th><th>DG</th></tr>
  <tr><td>7</td><td>3</td><td>2</td><td>1</td><td>0</td><td>6</td><td>2</td><td>+4</td></tr>
</table>
"""

SAMPLE_TEAM_HTML = """
<div class="jugador tipo_lista">
  <span class="nombre">Vinicius Junior</span>
  <a class="camiseta" data-nombre="Vinicius Junior" data-bpp="2002" data-bpfd="1001" data-bpfc="1000" data-bpc="3005"></a>
</div>
<div class="jugador tipo_lista">
  <span class="nombre">Kylian Mbappé</span>
  <a class="camiseta" data-nombre="Kylian Mbappé" data-bpp="3005" data-bpfd="2004" data-bpfc="1000" data-bpc="1000"></a>
</div>
<div class="jugador tipo_lista">
  <span class="nombre">Arda Güler</span>
  <a class="camiseta" data-nombre="Arda Güler" data-bpp="1000" data-bpfd="3001" data-bpfc="4002" data-bpc="5006"></a>
</div>
"""

SAMPLE_SANCTIONS_HTML = """
<header>Real Madrid</header>
<div class="elemento sancionado">
  <a class="jugador" href="/jugadores/test-player">Test Player</a>
  <span>Roja directa (1 partido)</span>
</div>
"""

SAMPLE_APERCIBIDOS_HTML = """
<header>Barcelona</header>
<div class="elemento apercibido">
  <a class="jugador" href="/jugadores/gavi">Gavi</a>
  <span>4 tarjetas amarillas</span>
</div>
"""

def test_parse_real_standings():
    rows = parse_real_standings_html(SAMPLE_STANDINGS_HTML)
    assert len(rows) == 1
    assert rows[0]["position"] == 1
    assert rows[0]["team"] == "Real Madrid"
    assert rows[0]["total"]["points"] == 13
    assert rows[0]["total"]["played"] == 5
    assert rows[0]["total"]["won"] == 4
    assert rows[0]["total"]["goalDifference"] == 8
    assert rows[0]["home"]["points"] == 6
    assert rows[0]["away"]["points"] == 7
    assert rows[0]["formString"] == "WD"

def test_parse_team_set_pieces():
    sp = parse_team_set_pieces(SAMPLE_TEAM_HTML, "real-madrid")
    assert sp["slug"] == "real-madrid"
    assert sp["penalties"] == ["Kylian Mbappé", "Vinicius Junior"]
    assert sp["directFouls"] == ["Arda Güler", "Kylian Mbappé", "Vinicius Junior"]
    assert sp["corners"] == ["Arda Güler", "Vinicius Junior"]

def test_parse_sanctions_and_apercibidos():
    sanctions = parse_sanctions_html(SAMPLE_SANCTIONS_HTML)
    assert len(sanctions) == 1
    assert sanctions[0]["name"] == "Test Player"
    assert sanctions[0]["team"] == "Real Madrid"
    assert "Roja directa" in sanctions[0]["reason"]

    apercibidos = parse_apercibidos_html(SAMPLE_APERCIBIDOS_HTML)
    assert len(apercibidos) == 1
    assert apercibidos[0]["name"] == "Gavi"
    assert apercibidos[0]["team"] == "Barcelona"
    assert "4 tarjetas" in apercibidos[0]["reason"]
