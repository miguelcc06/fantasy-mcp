"""Validación en profundidad de todas las tools MCP. No imprime secretos."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
OUT = ROOT / "scripts" / "_validation_report.json"

SECRET_RE = re.compile(
    r"(eyJ[\w\-]+\.[\w\-]+\.[\w\-]+)|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)


def redact(text: str) -> str:
    text = SECRET_RE.sub("[redacted]", text)
    for key in ("LALIGA_FANTASY_TOKEN", "LALIGA_FANTASY_REFRESH_TOKEN"):
        value = os.getenv(key) or ""
        if len(value) > 8:
            text = text.replace(value, "[redacted]")
    return text


def env_status() -> dict[str, bool]:
    return {
        "has_token": bool(os.getenv("LALIGA_FANTASY_TOKEN")),
        "has_refresh": bool(os.getenv("LALIGA_FANTASY_REFRESH_TOKEN")),
        "has_client_id": bool(os.getenv("LALIGA_FANTASY_CLIENT_ID")),
        "has_league_id": bool(os.getenv("LALIGA_FANTASY_LEAGUE_ID")),
    }


CASES: list[dict] = [
    {"name": "my_team_md", "tool": "laliga_get_my_team", "args": {}},
    {"name": "my_team_json", "tool": "laliga_get_my_team", "args": {"response_format": "json"}},
    {"name": "standings", "tool": "laliga_get_standings", "args": {}},
    {"name": "standings_json", "tool": "laliga_get_standings", "args": {"response_format": "json"}},
    {"name": "rivals_list", "tool": "laliga_get_rivals_teams", "args": {}},
    {"name": "fixtures_default", "tool": "laliga_get_fixtures", "args": {}},
    {"name": "fixtures_week1", "tool": "laliga_get_fixtures", "args": {"week": 1}},
    {"name": "fixtures_week2_json", "tool": "laliga_get_fixtures", "args": {"week": 2, "response_format": "json"}},
    {"name": "market", "tool": "laliga_get_market", "args": {}},
    {"name": "market_json", "tool": "laliga_get_market", "args": {"response_format": "json"}},
    {"name": "search_mbappe", "tool": "laliga_search_players", "args": {"query": "Mbappé", "limit": 5}},
    {"name": "search_mbappe_json", "tool": "laliga_search_players", "args": {"query": "Mbappé", "limit": 5, "response_format": "json"}},
    {"name": "search_portero", "tool": "laliga_search_players", "args": {"position": "portero", "limit": 5}},
    {"name": "search_team", "tool": "laliga_search_players", "args": {"team": "Real Madrid", "position": "delantero", "limit": 5}},
    {"name": "search_pagination", "tool": "laliga_search_players", "args": {"query": "a", "limit": 3, "offset": 3}},
    {"name": "player_by_name", "tool": "laliga_get_player_details", "args": {"player_id_or_name": "Mbappé"}},
    {"name": "lineups_madrid", "tool": "laliga_get_probable_lineups", "args": {"team_or_match": "real-madrid"}},
    {"name": "lineups_match", "tool": "laliga_get_probable_lineups", "args": {"team_or_match": "Barcelona vs Real Madrid"}},
    {"name": "lineups_unknown", "tool": "laliga_get_probable_lineups", "args": {"team_or_match": "zzz-no-existe"}},
    {"name": "lineups_girona", "tool": "laliga_get_probable_lineups", "args": {"team_or_match": "girona"}},
    {"name": "lineups_all", "tool": "laliga_get_probable_lineups", "args": {}, "preview": 8000},
    {"name": "trends_all", "tool": "laliga_get_market_trends", "args": {"limit": 5}},
    {"name": "trends_falling", "tool": "laliga_get_market_trends", "args": {"filter": "falling", "limit": 5}},
    {"name": "trends_rising_del", "tool": "laliga_get_market_trends", "args": {"filter": "rising", "position": "delantero", "limit": 5}},
    {"name": "injuries", "tool": "laliga_get_injuries", "args": {}},
    {"name": "formations", "tool": "laliga_get_formations", "args": {}},
    {"name": "week_standings_default", "tool": "laliga_get_week_standings", "args": {}},
    {"name": "week_standings_1", "tool": "laliga_get_week_standings", "args": {"week": 1}},
    {"name": "activity_p0", "tool": "laliga_get_league_activity", "args": {"page": 0, "limit": 10}},
    {"name": "activity_p1", "tool": "laliga_get_league_activity", "args": {"page": 1, "limit": 10}},
    {"name": "market_history", "tool": "laliga_get_market_history", "args": {"limit": 10}},
    {"name": "ownership", "tool": "laliga_get_ownership", "args": {"limit": 10}},
    {"name": "ownership_unlocked", "tool": "laliga_get_ownership", "args": {"query": "unlocked", "limit": 5}},
    {"name": "lineup_me", "tool": "laliga_get_lineup", "args": {}},
    {"name": "lineup_me_json", "tool": "laliga_get_lineup", "args": {"response_format": "json"}},
    {"name": "lineup_me_week1", "tool": "laliga_get_lineup", "args": {"week": 1}},
    {"name": "match_stats_1", "tool": "laliga_get_match_stats", "args": {"week": 1}},
    {"name": "match_stats_json", "tool": "laliga_get_match_stats", "args": {"week": 1, "response_format": "json"}},
    {"name": "balances", "tool": "laliga_calculate_rivals_balances", "args": {}},
]


def classify(text: str) -> str:
    lowered = text.lower()
    if text.startswith("Error:") or "error executing tool" in lowered:
        return "error"
    if "sin alineación" in lowered or "aún no hay" in lowered:
        return "ok_empty"
    if "no se encontró" in lowered or "no reconozco" in lowered:
        return "ok_empty"
    if "no está en laliga esta temporada" in lowered:
        return "ok_empty"
    return "ok"


def quality_issues(report: list[dict]) -> list[str]:
    by_name = {row.get("name"): row for row in report}
    issues: list[str] = []
    search = by_name.get("search_mbappe_json") or by_name.get("search_mbappe")
    if search and search.get("status") == "ok":
        preview = search.get("preview") or ""
        if '"total": 840' in preview or "de 840" in preview:
            issues.append("search_mbappe devolvió el catálogo entero")
        if "Mbappé" not in preview and "Mbappe" not in preview and "mbappé" not in preview.lower():
            issues.append("search_mbappe no menciona a Mbappé")
    lineup = by_name.get("lineup_me_json") or by_name.get("lineup_me")
    if lineup and lineup.get("status") == "ok":
        preview = lineup.get("preview") or ""
        if '"starters": []' in preview or preview.strip().endswith("Tu equipo"):
            issues.append("lineup propia vacía")
        if '"captain"' in preview or '"bench"' in preview:
            issues.append("lineup JSON aún expone captain/bench")
    market_json = by_name.get("market_json")
    if market_json and market_json.get("status") == "ok":
        preview = market_json.get("preview") or ""
        if re.search(r'"listing": "sale",\s*"label": "Libre"', preview):
            issues.append("venta de rival etiquetada como Libre")
    activity_p0 = by_name.get("activity_p0")
    activity_p1 = by_name.get("activity_p1")
    if activity_p0 and activity_p1 and activity_p0.get("status") == "ok":
        p0_items = len(re.findall(r"\n- ", activity_p0.get("preview") or ""))
        if p0_items >= 5 and "Sin eventos" in (activity_p1.get("preview") or ""):
            issues.append("paginación de actividad: page=1 vacía con page=0 llena")
    stats = by_name.get("match_stats_1")
    if stats and stats.get("status") == "ok":
        preview = stats.get("preview") or ""
        if "usa response_format=json" in preview.lower() or "hay datos oficiales" in preview.lower():
            issues.append("match_stats markdown sigue siendo un stub")
        if " vs " not in preview:
            issues.append("match_stats markdown no lista partidos")
    offers_free = by_name.get("player_offers_free")
    if offers_free and offers_free.get("status") in {"ok", "ok_empty"}:
        preview = (offers_free.get("preview") or "").lower()
        if "playerteamid" in preview and "no se encontró" in preview:
            issues.append("ofertas de libre no resuelven el listing de bolsa")
    lineup_rival = by_name.get("lineup_rival")
    if lineup_rival and lineup_rival.get("status") == "ok":
        preview = lineup_rival.get("preview") or ""
        if "403" in preview:
            issues.append("XI rival sigue respondiendo 403")
    activity = by_name.get("activity_p0")
    if activity and activity.get("status") == "ok":
        preview = activity.get("preview") or ""
        if "-  vendió" in preview or "-  fichó" in preview:
            issues.append("actividad sin nombres de mánager/jugador")
    balances = by_name.get("balances")
    if balances and balances.get("status") == "ok":
        preview = balances.get("preview") or ""
        if re.search(r"- \d{6,}:", preview):
            issues.append("saldos muestran IDs en vez de nombres")
    history = by_name.get("history_query")
    if history and history.get("status") == "ok":
        preview = history.get("preview") or ""
        if "- ? ·" in preview:
            issues.append("histórico de mercado sin nombres")
    history_mgr = by_name.get("history_query_manager")
    if history_mgr and history_mgr.get("status") == "ok":
        preview = history_mgr.get("preview") or ""
        if "(0)" in preview.split("\n", 1)[0] or "miguel" not in preview.lower():
            issues.append("histórico no filtra por mánager")
    unknown = by_name.get("player_unknown")
    if unknown and unknown.get("status") == "ok":
        issues.append("player_unknown debería no resolver a un jugador real")
    garbage = by_name.get("player_garbage")
    if garbage and garbage.get("status") == "ok":
        issues.append("player_garbage resolvió un jugador a partir de una query basura")
    lineups_madrid = by_name.get("lineups_madrid")
    if lineups_madrid and lineups_madrid.get("status") == "ok":
        preview = lineups_madrid.get("preview") or ""
        if not re.search(r"\d+%", preview):
            issues.append("onces Madrid sin porcentajes de titularidad")
        if "suplentes" not in preview.lower():
            issues.append("onces Madrid markdown no lista suplentes")
    lineups_all = by_name.get("lineups_all")
    if lineups_all and lineups_all.get("status") == "ok":
        preview = lineups_all.get("preview") or ""
        for bad in ("## Girona", "## Mallorca", "## Oviedo", "## oviedo"):
            if bad in preview:
                issues.append(f"lineups_all incluye {bad.lstrip('# ').strip()}")
        if re.search(r"- \d+[.,]\d{2}(?:\s| \[)", preview):
            issues.append("lineups_all tiene nombres tipo altura")
        headings = re.findall(r"^## .+$", preview, re.M)
        if len(headings) > 20:
            issues.append(f"lineups_all tiene {len(headings)} equipos (esperado ≤20)")
    lineups_girona = by_name.get("lineups_girona")
    if lineups_girona:
        preview = (lineups_girona.get("preview") or "").lower()
        if "no está en laliga esta temporada" not in preview:
            issues.append("lineups_girona no avisa que está fuera de LaLiga")
    market_history = by_name.get("market_history")
    activity_p0 = by_name.get("activity_p0")
    if (
        market_history
        and activity_p0
        and market_history.get("status") == "ok"
        and activity_p0.get("status") == "ok"
    ):
        marketish = len(re.findall(r"vendió|fichó|compró|clausuló", activity_p0.get("preview") or "", re.I))
        bullets = len(re.findall(r"\n- ", market_history.get("preview") or ""))
        if marketish >= 5 and bullets < 5:
            issues.append("market_history no cubre la liga (pocos movimientos)")
    formations = by_name.get("formations")
    if formations and formations.get("status") == "ok":
        preview = formations.get("preview") or ""
        if "['5,4,1'" in preview:
            issues.append("formaciones sin formatear")
    return issues


async def call(session: ClientSession, tool: str, args: dict, preview_chars: int = 800) -> dict:
    try:
        result = await asyncio.wait_for(session.call_tool(tool, args), timeout=90)
        text = ""
        if result.content:
            text = result.content[0].text or ""
        text = redact(text)
        status = classify(text)
        if getattr(result, "isError", False):
            status = "error"
        return {
            "tool": tool,
            "args": args,
            "status": status,
            "chars": len(text),
            "preview": text[:preview_chars],
        }
    except Exception as exc:
        return {
            "tool": tool,
            "args": args,
            "status": "exception",
            "chars": 0,
            "preview": redact(str(exc))[:400],
        }


async def main() -> int:
    status = env_status()
    print("env", json.dumps(status))
    if not status["has_token"] and not status["has_refresh"]:
        print("Falta TOKEN/REFRESH en .env", file=sys.stderr)
        return 2

    params = StdioServerParameters(
        command=str(PYTHON),
        args=["-m", "laliga_fantasy_mcp"],
        cwd=str(ROOT),
    )
    report: list[dict] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("listed", len(names), "tools")
            report.append({"name": "list_tools", "status": "ok" if len(names) == 19 else "error", "preview": ",".join(names)})

            # First pass: core context to derive rival/player ids
            for case in CASES:
                row = await call(
                    session,
                    case["tool"],
                    case["args"],
                    preview_chars=int(case.get("preview") or 800),
                )
                row["name"] = case["name"]
                report.append(row)
                flag = "OK" if row["status"].startswith("ok") else "FAIL"
                print(f"{flag:4} {case['name']:24} {row['status']:10} {row['chars']:5}c")

            # Follow-up cases using live data from previous JSON-ish previews
            standings = next((r for r in report if r["name"] == "standings_json"), None)
            extra: list[dict] = []
            rival_name = None
            if standings and standings["status"] == "ok":
                # Try to extract a manager name from markdown standings instead
                md = next((r for r in report if r["name"] == "standings"), None)
                if md:
                    match = re.search(r"1\.\s+(.+?)\s+—", md["preview"])
                    if match:
                        rival_name = match.group(1).strip()
            if rival_name:
                extra.append(
                    {"name": "rivals_query", "tool": "laliga_get_rivals_teams", "args": {"query": rival_name}}
                )
                extra.append(
                    {"name": "lineup_rival", "tool": "laliga_get_lineup", "args": {"team_or_manager": rival_name}}
                )
            extra.append(
                {"name": "player_offers_name", "tool": "laliga_get_player_offers", "args": {"player_id_or_name": "Mbappé"}}
            )
            market_md = next((r for r in report if r["name"] == "market"), None)
            if market_md:
                free_match = re.search(r"- (.+?) · .+ · Libre", market_md.get("preview") or "")
                if free_match:
                    extra.append(
                        {
                            "name": "player_offers_free",
                            "tool": "laliga_get_player_offers",
                            "args": {"player_id_or_name": free_match.group(1).strip()},
                        }
                    )
            extra.append(
                {"name": "search_value_range", "tool": "laliga_search_players", "args": {"min_value": 10000000, "max_value": 50000000, "limit": 5}}
            )
            extra.append(
                {"name": "search_bad_position", "tool": "laliga_search_players", "args": {"position": "9", "limit": 3}, "expected": "error"}
            )
            extra.append(
                {"name": "invalid_week", "tool": "laliga_get_fixtures", "args": {"week": 0}, "expected": "error"}
            )
            extra.append(
                {"name": "player_unknown", "tool": "laliga_get_player_details", "args": {"player_id_or_name": "xyzzy-no-player"}}
            )
            extra.append(
                {
                    "name": "player_garbage",
                    "tool": "laliga_get_player_details",
                    "args": {"player_id_or_name": "xyznoexiste123"},
                }
            )
            extra.append(
                {"name": "invalid_week_high", "tool": "laliga_get_week_standings", "args": {"week": 99}, "expected": "error"}
            )
            extra.append(
                {"name": "history_query", "tool": "laliga_get_market_history", "args": {"query": "Noubi", "limit": 5}}
            )
            extra.append(
                {
                    "name": "history_query_manager",
                    "tool": "laliga_get_market_history",
                    "args": {"query": "miguel", "limit": 5},
                }
            )
            for case in extra:
                row = await call(
                    session,
                    case["tool"],
                    case["args"],
                    preview_chars=int(case.get("preview") or 800),
                )
                row["name"] = case["name"]
                if case.get("expected") == "error" and row["status"] == "error":
                    row["status"] = "ok_expected_error"
                report.append(row)
                flag = "OK" if row["status"].startswith("ok") else "FAIL"
                print(f"{flag:4} {case['name']:24} {row['status']:10} {row['chars']:5}c")

    issues = quality_issues(report)
    OUT.write_text(
        json.dumps({"env": status, "results": report, "quality": issues}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    fails = [r for r in report if r.get("status") in {"error", "exception"}]
    print("FAILS", len(fails), "of", len(report))
    for row in fails:
        print(" -", row.get("name"), row.get("preview")[:180])
    print("QUALITY", len(issues))
    for issue in issues:
        print(" -", issue)
    return 1 if fails or issues else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
