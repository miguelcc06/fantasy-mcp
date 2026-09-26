"""Constantes y carga de entorno."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

API_BASE_URL = "https://fantasy-api.llt-services.com/api"
STATS_BASE_URL = "https://fantasy-api.llt-services.com"
AUTH_TOKEN_URL = (
    "https://login.laliga.es/laligadspprob2c.onmicrosoft.com/oauth2/v2.0/token"
    "?p=B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN"
)
COMPETITION_ID = os.getenv("LALIGA_FANTASY_COMPETITION_ID", "1")
CMP = f"/v1/competition/{COMPETITION_ID}"

# Política de pujas (LALIGA_FANTASY_BID_POLICY). Las tools releen el entorno con get_bid_policy().
# readonly: prohibido crear y actualizar. update_own: solo actualizar pujas propias.
# create_and_update: crear pujas nuevas y actualizar las existentes.
BID_POLICY_DEFAULT = "update_own"
VALID_BID_POLICIES = frozenset({"readonly", "update_own", "create_and_update"})
LALIGA_FANTASY_BID_POLICY = os.getenv("LALIGA_FANTASY_BID_POLICY", BID_POLICY_DEFAULT)

DEFAULT_WEB_CLIENT_ID = "6457fa17-1224-416a-b21a-ee6ce76e9bc0"
DEFAULT_NATIVE_CLIENT_ID = "af88bcff-1157-40a0-b579-030728aacf0b"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

STARTING_BUDGET = 100_000_000
TEAM_VALUE_BID_BONUS = 0.20
# Cláusula inicial: 1 M€ si VM < 1 M€; si no, 166,67 % (5/3) del VM el día de alta.
CLAUSE_RATIO_NUM = 5
CLAUSE_RATIO_DEN = 3
CLAUSE_MIN = 1_000_000
# Subir cláusula cuesta 1 € por cada 2 € de incremento (ligas estándar).
CLAUSE_INCREASE_PAY_RATIO = 2
DAILY_REWARD_FREE = 100_000
TOKEN_EXPIRY_SKEW_SECONDS = 5 * 60
HTTP_TIMEOUT = 20.0
LINEUP_CACHE_SECONDS = 30 * 60
TRENDS_CACHE_SECONDS = 6 * 60 * 60
INJURY_CACHE_SECONDS = 30 * 60
OWNERSHIP_CONCURRENCY = 3
SEARCH_ENRICH_CONCURRENCY = 5
WEEK_NUMBER_MAX = 42

FF_LINEUP_URL = "https://www.futbolfantasy.com/laliga/equipos/{slug}"
FF_INJURIES_URL = "https://www.futbolfantasy.com/laliga/lesionados"
FF_TRENDS_URL = "https://www.futbolfantasy.com/analytics/laliga-fantasy/mercado"
FF_STANDINGS_URL = "https://www.futbolfantasy.com/laliga/clasificacion"
FF_SANCTIONS_URL = "https://www.futbolfantasy.com/laliga/sancionados"
FF_APERCIBIDOS_URL = "https://www.futbolfantasy.com/laliga/apercibidos"
STANDINGS_CACHE_SECONDS = 30 * 60
SET_PIECES_CACHE_SECONDS = 60 * 60
SANCTIONS_CACHE_SECONDS = 30 * 60

LALIGA_TEAMS: dict[str, dict[str, str]] = {
    "alaves": {"name": "Alavés", "fullName": "Deportivo Alavés"},
    "athletic": {"name": "Athletic", "fullName": "Athletic Club"},
    "atletico": {"name": "Atlético", "fullName": "Atlético Madrid"},
    "barcelona": {"name": "Barcelona", "fullName": "FC Barcelona"},
    "betis": {"name": "Betis", "fullName": "Real Betis Balompié"},
    "celta": {"name": "Celta", "fullName": "RC Celta de Vigo"},
    "deportivo": {"name": "Deportivo", "fullName": "RC Deportivo"},
    "elche": {"name": "Elche", "fullName": "Elche CF"},
    "espanyol": {"name": "Espanyol", "fullName": "RCD Espanyol"},
    "getafe": {"name": "Getafe", "fullName": "Getafe CF"},
    "girona": {"name": "Girona", "fullName": "Girona FC"},
    "levante": {"name": "Levante", "fullName": "Levante UD"},
    "mallorca": {"name": "Mallorca", "fullName": "RCD Mallorca"},
    "malaga": {"name": "Málaga", "fullName": "Málaga CF"},
    "osasuna": {"name": "Osasuna", "fullName": "CA Osasuna"},
    "oviedo": {"name": "Oviedo", "fullName": "Real Oviedo"},
    "racing": {"name": "Racing", "fullName": "R. Racing Club"},
    "rayo-vallecano": {"name": "Rayo", "fullName": "Rayo Vallecano"},
    "real-madrid": {"name": "Real Madrid", "fullName": "Real Madrid CF"},
    "real-sociedad": {"name": "Real Sociedad", "fullName": "Real Sociedad de Fútbol"},
    "sevilla": {"name": "Sevilla", "fullName": "Sevilla FC"},
    "valencia": {"name": "Valencia", "fullName": "Valencia CF"},
    "villarreal": {"name": "Villarreal", "fullName": "Villarreal CF"},
}

# LaLiga 2026/27 (sin Girona, Mallorca ni Oviedo). Fallback si el calendario falla.
CURRENT_LALIGA_SLUGS: tuple[str, ...] = (
    "alaves",
    "athletic",
    "atletico",
    "barcelona",
    "betis",
    "celta",
    "deportivo",
    "elche",
    "espanyol",
    "getafe",
    "levante",
    "malaga",
    "osasuna",
    "racing",
    "rayo-vallecano",
    "real-madrid",
    "real-sociedad",
    "sevilla",
    "valencia",
    "villarreal",
)

FF_SLUG_ALIASES: dict[str, str] = {
    "oviedo": "real-oviedo",
    "real-oviedo": "real-oviedo",
    "rayo": "rayo-vallecano",
}

MARKET_ACTIVITY_TYPES = {1, 31, 32, 33}
OPERATION_FROM_ACTIVITY = {1: "purchase", 31: "purchase", 32: "clause", 33: "sale"}

ACTIVITY_LABELS = {
    1: "compró",
    4: "blindó",
    6: "prima de jornada",
    7: "alineación incorrecta",
    9: "nuevo miembro",
    31: "fichó",
    32: "clausuló",
    33: "vendió",
}

POSITION_NAMES = {1: "Portero", 2: "Defensa", 3: "Centrocampista", 4: "Delantero"}


def get_bid_policy() -> str:
    """Política efectiva de pujas.

    Vacío o ausente equivale a ``update_own``. Un valor desconocido lanza ValueError
    para no pujar con una configuración ambigua.
    """
    raw = os.getenv("LALIGA_FANTASY_BID_POLICY")
    if raw is None or not raw.strip():
        return BID_POLICY_DEFAULT
    policy = raw.strip().lower()
    if policy not in VALID_BID_POLICIES:
        allowed = ", ".join(sorted(VALID_BID_POLICIES))
        raise ValueError(
            f"LALIGA_FANTASY_BID_POLICY={raw.strip()!r} no es válida. Usa: {allowed}."
        )
    return policy
