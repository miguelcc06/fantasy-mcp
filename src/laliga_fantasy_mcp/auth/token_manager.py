"""Gestión headless de JWT / refresh token de LaLiga Fantasy."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from laliga_fantasy_mcp.auth.electron_reader import load_electron_tokens
from laliga_fantasy_mcp.config import (
    AUTH_TOKEN_URL,
    DEFAULT_NATIVE_CLIENT_ID,
    DEFAULT_WEB_CLIENT_ID,
    HTTP_TIMEOUT,
    TOKEN_EXPIRY_SKEW_SECONDS,
    USER_AGENT,
)

logger = logging.getLogger("laliga_fantasy_mcp.auth")


def _decode_exp(token: str | None) -> int | None:
    if not token or token.count(".") < 2:
        return None
    import base64
    import json

    payload = token.split(".")[1]
    pad = "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + pad).decode("utf-8"))
    except Exception:
        return None
    exp = data.get("exp")
    return int(exp) if exp else None


class TokenManager:
    """Carga tokens de env/.env, con bootstrap opcional desde Electron."""

    def __init__(self) -> None:
        self._access: str | None = None
        self._refresh: str | None = None
        self._client_id: str | None = None
        self._expires_on: int | None = None
        self._lock_refresh = False

    def load(self) -> None:
        token = os.getenv("LALIGA_FANTASY_TOKEN") or ""
        refresh = os.getenv("LALIGA_FANTASY_REFRESH_TOKEN") or ""
        client_id = os.getenv("LALIGA_FANTASY_CLIENT_ID") or ""
        source = "env"
        if not token and not refresh:
            electron, origin = load_electron_tokens()
            if electron:
                token = electron.get("access_token") or electron.get("id_token") or ""
                refresh = electron.get("refresh_token") or ""
                client_id = electron.get("client_id") or client_id
                if electron.get("expires_on"):
                    try:
                        self._expires_on = int(electron["expires_on"])
                    except (TypeError, ValueError):
                        self._expires_on = None
                source = origin or "electron"
        if not token and not refresh:
            raise RuntimeError(
                "No hay sesión de LaLiga Fantasy. Define LALIGA_FANTASY_TOKEN y "
                "LALIGA_FANTASY_REFRESH_TOKEN en .env (o ejecuta "
                "python scripts/export_tokens_to_env.py con LaLigaApp abierta)."
            )
        self._access = token or None
        self._refresh = refresh or None
        self._client_id = client_id or DEFAULT_WEB_CLIENT_ID
        if self._access and not self._expires_on:
            self._expires_on = _decode_exp(self._access)
        logger.info("Sesión cargada desde %s (refresh=%s)", source, "sí" if self._refresh else "no")

    def bearer(self) -> str:
        if not self._access:
            raise RuntimeError(
                "Token de acceso vacío. Revisa LALIGA_FANTASY_TOKEN o vuelve a exportar la sesión."
            )
        return self._access

    def is_expired(self) -> bool:
        if not self._access:
            return True
        exp = self._expires_on or _decode_exp(self._access)
        if not exp:
            return False
        return (exp - int(time.time())) < TOKEN_EXPIRY_SKEW_SECONDS

    async def ensure_fresh(self, client: httpx.AsyncClient | None = None) -> str:
        if self._access and not self.is_expired():
            return self._access
        if self._refresh:
            await self.refresh(client)
        return self.bearer()

    async def refresh(self, client: httpx.AsyncClient | None = None) -> None:
        if not self._refresh:
            raise RuntimeError(
                "El access token ha caducado y no hay refresh_token. "
                "Copia un LALIGA_FANTASY_REFRESH_TOKEN nuevo al .env."
            )
        if self._lock_refresh:
            return
        self._lock_refresh = True
        own_client = client is None
        http = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        last_error = "unknown"
        try:
            for client_id in self._client_candidates():
                try:
                    await self._refresh_with(http, client_id)
                    self._client_id = client_id
                    return
                except RuntimeError as exc:
                    last_error = str(exc)
                    continue
            raise RuntimeError(
                f"No se pudo renovar el token ({last_error}). "
                "Genera un refresh_token nuevo desde LaLigaApp y actualiza el .env."
            )
        finally:
            self._lock_refresh = False
            if own_client:
                await http.aclose()

    def _client_candidates(self) -> list[str]:
        ordered = [
            self._client_id,
            DEFAULT_NATIVE_CLIENT_ID,
            DEFAULT_WEB_CLIENT_ID,
        ]
        seen: list[str] = []
        for item in ordered:
            if item and item not in seen:
                seen.append(item)
        return seen

    async def _refresh_with(self, http: httpx.AsyncClient, client_id: str) -> None:
        response = await http.post(
            AUTH_TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh,
                "client_id": client_id,
                "scope": "openid offline_access",
            },
        )
        if response.status_code in {400, 401}:
            raise RuntimeError("invalid_grant")
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}")
        payload: dict[str, Any] = response.json()
        access = payload.get("access_token") or payload.get("id_token")
        if not access:
            raise RuntimeError("la respuesta de refresh no incluye token")
        self._access = access
        if payload.get("refresh_token"):
            self._refresh = payload["refresh_token"]
        if payload.get("id_token_expires_in"):
            self._expires_on = int(time.time()) + int(payload["id_token_expires_in"])
        elif payload.get("expires_in"):
            self._expires_on = int(time.time()) + int(payload["expires_in"])
        elif payload.get("expires_on"):
            self._expires_on = int(payload["expires_on"])
        else:
            self._expires_on = _decode_exp(access)
        logger.info("Token renovado contra B2C")
