"""Singletons de cliente HTTP y tokens."""

from __future__ import annotations

from laliga_fantasy_mcp.auth.token_manager import TokenManager
from laliga_fantasy_mcp.client.api import FantasyClient
from laliga_fantasy_mcp.client.futbolfantasy import FutbolFantasyClient

_tokens: TokenManager | None = None
_api: FantasyClient | None = None
_ff: FutbolFantasyClient | None = None


def tokens() -> TokenManager:
    global _tokens
    if _tokens is None:
        _tokens = TokenManager()
        _tokens.load()
    return _tokens


def api() -> FantasyClient:
    global _api
    if _api is None:
        _api = FantasyClient(tokens())
    return _api


def ff() -> FutbolFantasyClient:
    global _ff
    if _ff is None:
        _ff = FutbolFantasyClient()
    return _ff
