"""Scrapers de FútbolFantasy: onces, tendencias y lesionados."""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

from laliga_fantasy_mcp.config import (
    CURRENT_LALIGA_SLUGS,
    FF_INJURIES_URL,
    FF_LINEUP_URL,
    FF_SLUG_ALIASES,
    FF_TRENDS_URL,
    HTTP_TIMEOUT,
    INJURY_CACHE_SECONDS,
    LALIGA_TEAMS,
    LINEUP_CACHE_SECONDS,
    TRENDS_CACHE_SECONDS,
    USER_AGENT,
)
from laliga_fantasy_mcp.services.helpers import fold, position_id

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
}


class FutbolFantasyClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=_HEADERS, follow_redirects=True)
        self._lineup_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._injuries_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._trends_cache: tuple[float, list[dict[str, Any]]] | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get_html(self, url: str) -> str:
        response = await self._http.get(url)
        response.raise_for_status()
        return response.content.decode("utf-8", errors="replace")

    async def lineup(self, slug: str) -> dict[str, Any]:
        cached = self._lineup_cache.get(slug)
        now = time.time()
        if cached and now - cached[0] < LINEUP_CACHE_SECONDS:
            return cached[1]
        ff_slug = FF_SLUG_ALIASES.get(slug, slug)
        html = await self._get_html(FF_LINEUP_URL.format(slug=ff_slug))
        parsed = parse_lineup_html(html, slug)
        self._lineup_cache[slug] = (now, parsed)
        return parsed

    async def all_lineups(self, slugs: list[str] | None = None) -> list[dict[str, Any]]:
        targets = slugs or list(CURRENT_LALIGA_SLUGS)
        results: list[dict[str, Any]] = []
        for slug in targets:
            try:
                results.append(await self.lineup(slug))
            except Exception as exc:
                results.append({"slug": slug, "error": str(exc), "source": "futbolfantasy.com"})
        return results

    async def injuries(self) -> list[dict[str, Any]]:
        now = time.time()
        if self._injuries_cache and now - self._injuries_cache[0] < INJURY_CACHE_SECONDS:
            return self._injuries_cache[1]
        html = await self._get_html(FF_INJURIES_URL)
        parsed = parse_injuries_html(html)
        self._injuries_cache = (now, parsed)
        return parsed

    async def trends(self) -> list[dict[str, Any]]:
        now = time.time()
        if self._trends_cache and now - self._trends_cache[0] < TRENDS_CACHE_SECONDS:
            return self._trends_cache[1]
        html = await self._get_html(FF_TRENDS_URL)
        parsed = parse_market_trends(html)
        self._trends_cache = (now, parsed)
        return parsed


_HEIGHT_RE = re.compile(r"^\d+[.,]\d{2}$")
_NUMBER_NAME_RE = re.compile(r"^\d+([.,]\d+)?$")
_JUNK_NAME_RE = re.compile(r"^(alt|edad|nac|goles|asis|prob|estado|fd|fc|c|p)$", re.I)


def parse_percent(value: Any) -> int | None:
    """Convierte '70%' / '70' / 70 / '1.82 70%' en un entero 0-100."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number if 0 <= number <= 100 else None
    text = str(value).strip()
    percent_match = re.search(r"(\d+)\s*%", text)
    if percent_match:
        number = int(percent_match.group(1))
        return number if 0 <= number <= 100 else None
    if _HEIGHT_RE.match(text):
        return None
    if re.fullmatch(r"\d{1,3}", text):
        number = int(text)
        return number if 0 <= number <= 100 else None
    return None


def _is_junk_name(name: str | None) -> bool:
    text = re.sub(r"\s*\d+\s*%\s*$", "", (name or "")).strip()
    if len(text) < 2:
        return True
    if _HEIGHT_RE.match(text) or _NUMBER_NAME_RE.match(text):
        return True
    if _JUNK_NAME_RE.match(text):
        return True
    return False


def _clean_display_name(name: str) -> str:
    return re.sub(r"\s*\d+\s*%\s*$", "", name or "").strip()


def _pick_player_name(*candidates: str, href: str = "") -> str:
    for raw in candidates:
        name = _clean_display_name(raw)
        if name and not _is_junk_name(name):
            return name
    from_href = _slug_to_name(href)
    if from_href and not _is_junk_name(from_href):
        return from_href
    return ""


def _percent_from_node(*values: Any) -> int | None:
    for value in values:
        percent = parse_percent(value)
        if percent is not None:
            return percent
    return None


def parse_lineup_html(html: str, slug: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    team = LALIGA_TEAMS.get(slug, {"name": slug, "fullName": slug})
    coach_el = soup.select_one(".nombre-entrenador")
    starters: list[dict[str, Any]] = []
    bench: list[dict[str, Any]] = []

    sections = soup.select('[class*="jugadores-titulares"]')
    starter_nodes = sections[0].select(".jugador.tipo_lista") if sections else []
    for index, node in enumerate(starter_nodes):
        player = _player_from_list_node(node, index, True)
        if player:
            starters.append(player)
    bench_sections = soup.select('[class*="jugadores-suplentes"]')
    bench_nodes = bench_sections[0].select(".jugador.tipo_lista") if bench_sections else []
    for index, node in enumerate(bench_nodes):
        player = _player_from_list_node(node, index, False)
        if player:
            bench.append(player)

    if not starters:
        for wrapper in soup.select(".camiseta-wrapper[data-onceff], .camiseta-wrapper[data-onceFF]"):
            flag = (wrapper.get("data-onceff") or wrapper.get("data-onceFF") or "").lower()
            is_starter = flag == "titular"
            anchors = wrapper.select("a[href*='/jugadores/']")
            for idx, anchor in enumerate(anchors):
                player = _player_from_anchor(wrapper, anchor, is_starter and idx == 0)
                if not player:
                    continue
                if player["isStarter"]:
                    starters.append(player)
                else:
                    bench.append(player)

    starters = [player for player in starters if not _is_junk_name(player.get("name"))]
    bench = [player for player in bench if not _is_junk_name(player.get("name"))]
    note = None
    if not starters:
        note = "No se pudo leer el XI (nombres no disponibles en FútbolFantasy)."

    injured = [
        player["name"]
        for player in starters + bench
        if player.get("status") in {"injured", "suspended"}
    ]
    return {
        "slug": slug,
        "team": team["name"],
        "fullName": team["fullName"],
        "coach": coach_el.get_text(strip=True) if coach_el else None,
        "starters": starters[:11],
        "bench": bench,
        "absences": injured[:20],
        "note": note,
        "source": "futbolfantasy.com",
    }


def _player_from_list_node(node: Any, index: int, is_starter: bool) -> dict[str, Any] | None:
    name_el = node.select_one(".nombre, .player-name, .truncate-name, strong, a.jugador")
    raw_name = str(node.get("data-nombre") or "")
    visible = name_el.get_text(strip=True) if name_el else ""
    href = ""
    link_text = ""
    link = node.select_one("a[href*='/jugadores/']")
    if link:
        href = link.get("href") or ""
        link_text = link.get_text(strip=True)
    name = _pick_player_name(raw_name.replace("-", " "), visible, link_text, href=href)
    if not name:
        return None
    percent = _percent_from_node(
        node.get("data-probabilidad"),
        node.get("data-probability"),
        node.get_text(" ", strip=True),
    )
    lesion = str(node.get("data-lesion") or "")
    nodisp = str(node.get("data-nodisponible") or "0")
    status = "available"
    if lesion == "0" or nodisp == "1":
        status = "injured"
    elif lesion in {"1", "2"}:
        status = "doubt"
    return {
        "name": name,
        "nickname": name,
        "probability": percent,
        "isStarter": is_starter,
        "status": status,
        "positionId": position_id(node.get("data-posicion") or node.get("posicion")),
    }


def _player_from_anchor(wrapper: Any, anchor: Any, is_starter: bool) -> dict[str, Any] | None:
    display = ""
    trunc = anchor.select_one(".truncate-name")
    if trunc:
        display = trunc.get_text(strip=True)
    if not display:
        display = anchor.get_text(" ", strip=True)
    href = anchor.get("href") or ""
    name = _pick_player_name(display, href=href)
    if not name:
        return None
    lesion = anchor.get("data-lesion")
    status = "injured" if lesion == "0" else "doubt" if lesion in {"1", "2"} else "available"
    percent = _percent_from_node(
        wrapper.get("data-probabilidad"),
        wrapper.get("data-probability"),
        anchor.get("data-probabilidad"),
        anchor.get("data-probability"),
        display,
        anchor.get_text(" ", strip=True),
    )
    return {
        "name": name,
        "nickname": name,
        "probability": percent,
        "isStarter": is_starter,
        "status": status,
        "positionId": position_id(wrapper.get("data-posicion")),
    }


def _slug_to_name(href: str) -> str:
    match = re.search(r"/jugadores/([^/?#]+)", href or "")
    if not match:
        return ""
    return " ".join(part.capitalize() for part in match.group(1).replace("-", " ").split())


_FF_TYPOS = (("hsata", "hasta"), ("princpios", "principios"))


def _clean_ff_text(text: str) -> str:
    out = text or ""
    for bad, good in _FF_TYPOS:
        out = re.sub(bad, good, out, flags=re.IGNORECASE)
    return out


def parse_injuries_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Any]] = []
    cards = soup.select(".elemento.lesionado, .elemento.duda, .elemento.sancionado, .elemento")
    if not cards:
        cards = soup.select(".lesionado, .duda, .sancionado")
    for node in cards:
        name_el = node.select_one("a.jugador") or node.select_one("a[href*='/jugadores/']")
        name = name_el.get_text(strip=True) if name_el else ""
        if len(name) < 2 and name_el is not None:
            name = (name_el.get("href") or "").rsplit("/", 1)[-1].replace("-", " ").strip()
        if len(name) < 2:
            continue
        classes = " ".join(node.get("class") or [])
        text = node.get_text(" ", strip=True)
        lowered = fold(text + " " + classes)
        status = "injured"
        if "sancion" in lowered:
            status = "suspended"
        elif "duda" in lowered or "tocado" in lowered:
            status = "doubt"
        elif "disponible" in lowered or "recuperado" in lowered:
            status = "available"
        percent = None
        prob = node.select_one(".probabilidad-widget")
        source_text = prob.get_text(" ", strip=True) if prob else text
        match = re.search(r"(\d+)\s*%", source_text)
        if match:
            percent = int(match.group(1))
        comment = node.select_one(".comentario")
        team_header = node.find_previous("header")
        detail = comment.get_text(" ", strip=True) if comment else text
        rows.append(
            {
                "name": name,
                "team": team_header.get_text(" ", strip=True) if team_header else None,
                "status": status,
                "detail": _clean_ff_text(detail)[:240],
                "probability": percent,
                "source": "futbolfantasy.com",
            }
        )
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = fold(row["name"])
        if key not in unique or len(row["detail"]) > len(unique[key]["detail"]):
            unique[key] = row
    return list(unique.values())


def parse_market_trends(html: str) -> list[dict[str, Any]]:
    team_map = _team_mapping(html)
    players: list[dict[str, Any]] = []
    chunks = html.split('class="elemento_jugador')
    for chunk in chunks[1:]:
        nombre = _attr(chunk, "data-nombre")
        posicion = _attr(chunk, "data-posicion")
        valor = _attr(chunk, "data-valor")
        diff = _attr(chunk, "data-diferencia1")
        pct = _attr(chunk, "data-diferencia-pct1")
        equipo = _attr(chunk, "data-equipo")
        if not (nombre and posicion and valor and diff and pct):
            continue
        try:
            value = int(valor)
            difference = float(diff)
            percent = float(pct)
        except ValueError:
            continue
        team_name = team_map.get(equipo or "", "LaLiga")
        players.append(
            {
                "name": nombre,
                "position": posicion,
                "team": team_name,
                "value": value,
                "change": difference,
                "changePercent": percent,
                "direction": "up" if difference > 0 else "down" if difference < 0 else "stable",
            }
        )
    return players


def _attr(text: str, name: str) -> str | None:
    match = re.search(rf'{name}="([^"]+)"', text)
    return match.group(1) if match else None


def _team_mapping(html: str) -> dict[str, str]:
    mapping = {
        "1": "Athletic",
        "2": "Atlético",
        "3": "Barcelona",
        "4": "Betis",
        "5": "Celta",
        "7": "Espanyol",
        "8": "Getafe",
        "10": "Levante",
        "12": "Mallorca",
        "13": "Osasuna",
        "14": "Rayo",
        "15": "Real Madrid",
        "16": "Real Sociedad",
        "17": "Sevilla",
        "18": "Valencia",
        "21": "Elche",
        "22": "Villarreal",
        "28": "Alavés",
        "30": "Girona",
        "43": "Real Oviedo",
        "6": "Deportivo",
        "11": "Málaga",
        "42": "Racing",
    }
    select = re.search(r'<select[^>]*name="equipo"[^>]*>([\s\S]*?)</select>', html)
    if not select:
        return mapping
    for option in re.finditer(r'<option[^>]*value="(\d+)"[^>]*>([^<]+)</option>', select.group(1)):
        if option.group(1) != "0":
            mapping[option.group(1)] = option.group(2).strip()
    return mapping
