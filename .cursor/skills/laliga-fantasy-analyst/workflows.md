# Flujos de trabajo

Copiar el checklist y marcarlo.

## 1. Análisis de mercado y traspasos

```
- [ ] laliga_get_my_team (huecos, saldo, pujas disponibles)
- [ ] laliga_get_market
- [ ] laliga_get_market_trends (rising/falling)
- [ ] laliga_get_ownership (dueño, cláusula, bloqueo, blindaje)
- [ ] laliga_calculate_rivals_balances (quién puede superarte)
- [ ] laliga_get_player_details del objetivo
- [ ] laliga_get_probable_lineups y laliga_get_injuries
- [ ] Noticias en 2-3 fuentes de sources.md
```

Propuesta de compra: precio máximo, techo del rival, plan si sale una cláusula.
Propuesta de venta: a máquina vs mercado, timing vs tendencia de valor.

## 2. Alineación de jornada

```
- [ ] laliga_get_fixtures (si no hay week, la siguiente)
- [ ] laliga_get_my_team / laliga_get_lineup
- [ ] laliga_get_formations
- [ ] laliga_get_probable_lineups (tus equipos y rivales)
- [ ] laliga_get_injuries
- [ ] laliga_get_player_details de dudas
```

Salida: XI titular, formación, riesgos (rotación, sanción, 0% titular).

## 3. Jugador a jugador

```
- [ ] laliga_search_players o laliga_get_player_details
- [ ] Tendencia de valor
- [ ] Ownership (si está en tu liga)
- [ ] Onces + lesiones del equipo real
- [ ] Calendario (laliga_get_fixtures)
```

## 4. Detectar regresos baratos

Buscar lesionados/sancionados que:

1. Están o van a estar disponibles (`laliga_get_injuries`).
2. Tienen % alto de titularidad en FútbolFantasy.
3. Han bajado de valor (`laliga_get_market_trends` falling).
4. Están libres o con dueño con poca liquidez.

## 5. Vigilancia de rivales

```
- [ ] laliga_get_rivals_teams
- [ ] laliga_get_lineup (week anterior)
- [ ] laliga_get_league_activity / laliga_get_market_history
- [ ] laliga_get_week_standings
```
