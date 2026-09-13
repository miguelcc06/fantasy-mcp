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

## Herramientas (todas read-only)

| Tool | Uso |
| --- | --- |
| `laliga_get_my_team` | Plantilla, XI, saldo y dinero para pujas |
| `laliga_get_standings` | Clasificación general |
| `laliga_get_rivals_teams` | Plantillas rivales |
| `laliga_calculate_rivals_balances` | Saldo base + techo de recompensas diarias |
| `laliga_get_player_details` | Ficha por ID o nombre |
| `laliga_get_probable_lineups` | Onces FútbolFantasy |
| `laliga_get_fixtures` | Partidos de una jornada |
| `laliga_get_market` | Mercado actual |
| `laliga_search_players` | Catálogo filtrable |
| `laliga_get_market_trends` | Subidas/bajadas diarias |
| `laliga_get_ownership` | Dueños y cláusulas |
| `laliga_get_player_offers` | Ofertas sobre un jugador |
| `laliga_get_league_activity` | Feed de la liga |
| `laliga_get_market_history` | Histórico de mercado |
| `laliga_get_week_standings` | Clasificación de jornada |
| `laliga_get_lineup` | Alineación por jornada |
| `laliga_get_match_stats` | Stats oficiales |
| `laliga_get_formations` | Formaciones legales |
| `laliga_get_injuries` | Bajas y dudas |

## Skill del agente

[`.cursor/skills/laliga-fantasy-analyst/SKILL.md`](.cursor/skills/laliga-fantasy-analyst/SKILL.md) describe flujos de mercado, alineación y fuentes fiables.

## Seguridad

- `.env` y `LaLigaApp/` están en `.gitignore`.
- No se registran JWT en logs.
- No hay tools de escritura (pujas, ventas, XI, cláusulas).
