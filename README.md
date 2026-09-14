# laliga_fantasy_mcp

Servidor MCP de **solo lectura** para [LaLiga Fantasy](https://fantasy.laliga.com). Permite a un agente (Cursor, OpenClaw u otro cliente MCP) consultar plantilla, mercado, rivales, onces probables y más, sin pujar ni cambiar alineaciones.

## Requisitos

- Python 3.10+
- Una sesión de LaLiga Fantasy (token + refresh token)

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Copia [`.env.example`](.env.example) a `.env` y rellena:

```
LALIGA_FANTASY_TOKEN=...
LALIGA_FANTASY_REFRESH_TOKEN=...
LALIGA_FANTASY_CLIENT_ID=...
LALIGA_FANTASY_LEAGUE_ID=   # opcional
```

Si tienes LaLigaApp abierta y autenticada en el mismo PC:

```bash
python scripts/export_tokens_to_env.py
```

El script busca `laliga_auth_tokens.json` en `%APPDATA%` / `%LOCALAPPDATA%` y escribe `.env` **sin imprimir secretos**. Si no hay sesión local, copia el access/refresh token a `.env` a mano.

## Arranque (stdio)

```bash
python -m laliga_fantasy_mcp
```

Cursor ya queda configurado en [`.cursor/mcp.json`](.cursor/mcp.json). Reinicia MCP o Cursor tras instalar.

Inspector:

```bash
npx @modelcontextprotocol/inspector python -m laliga_fantasy_mcp
```

## OpenClaw (servidor aparte)

No hace falta LaLigaApp en el host. Copia las mismas variables al entorno del MCP:

```json
{
  "mcpServers": {
    "laliga_fantasy": {
      "command": "python",
      "args": ["-m", "laliga_fantasy_mcp"],
      "env": {
        "LALIGA_FANTASY_TOKEN": "...",
        "LALIGA_FANTASY_REFRESH_TOKEN": "...",
        "LALIGA_FANTASY_CLIENT_ID": "..."
      }
    }
  }
}
```

El servidor renueva el JWT contra Azure AD B2C. Si el refresh caduca, genera uno nuevo con LaLigaApp y actualiza el `.env` (una vez).

## Herramientas disponibles

| Tool | Uso | Tipo |
| --- | --- | --- |
| `laliga_get_my_team` | Plantilla, XI, saldo y dinero para pujas | Read-Only |
| `laliga_get_standings` | Clasificación general de la liga privada | Read-Only |
| `laliga_get_rivals_teams` | Plantillas rivales y snapshot de jornada | Read-Only |
| `laliga_calculate_rivals_balances` | Saldo base + techo de recompensas diarias | Read-Only |
| `laliga_get_player_details` | Ficha por ID o nombre (stats, historial) | Read-Only |
| `laliga_get_probable_lineups` | Onces probables FútbolFantasy con % titularidad | Read-Only |
| `laliga_get_fixtures` | Enfrentamientos y horarios de una jornada | Read-Only |
| `laliga_get_market` | Mercado actual (libres, ventas y pujas activas) | Read-Only |
| `laliga_search_players` | Catálogo filtrable por equipo, posición, valor | Read-Only |
| `laliga_get_market_trends` | Subidas/bajadas diarias de valor | Read-Only |
| `laliga_get_ownership` | Dueños, cláusulas, bloqueos y blindajes | Read-Only |
| `laliga_get_player_offers` | Ofertas sobre un jugador | Read-Only |
| `laliga_get_league_activity` | Feed de actividad de la liga | Read-Only |
| `laliga_get_market_history` | Histórico de mercado de toda la liga | Read-Only |
| `laliga_get_week_standings` | Clasificación de jornada concreta | Read-Only |
| `laliga_get_lineup` | Alineación por jornada | Read-Only |
| `laliga_get_match_stats` | Stats oficiales de partidos | Read-Only |
| `laliga_get_formations` | Formaciones tácticas legales (free/premium) | Read-Only |
| `laliga_get_injuries` | Bajas, dudas y lesionados | Read-Only |
| `laliga_get_real_standings_and_form` | Clasificación oficial LaLiga EA Sports en tiempo real (puntos, GF/GC, local/visitante y racha de 5 partidos) | Read-Only |
| `laliga_get_set_piece_takers` | Jerarquía de lanzadores a balón parado (penaltis, faltas directas, indirectas, córners) por club | Read-Only |
| `laliga_get_sanctions_and_cards` | Jugadores sancionados federativamente y apercibidos (4 tarjetas amarillas) | Read-Only |
| `laliga_get_squad_aggregate_stats` | Resumen estadístico agregado de plantilla (puntos, local/visitante, titularidades, minutos, tendencia 7d) | Read-Only |
| `laliga_set_lineup` | Modificar la alineación activa del usuario (formación táctica, 11 titulares, capitán y suplentes) | Write |

## Skill del agente

[`.cursor/skills/laliga-fantasy-analyst/SKILL.md`](.cursor/skills/laliga-fantasy-analyst/SKILL.md) describe flujos de mercado, alineación y fuentes fiables.

## Seguridad

- `.env` y `LaLigaApp/` están en `.gitignore`.
- No se registran JWT en logs.
- No hay tools de escritura (pujas, ventas, XI, cláusulas).
