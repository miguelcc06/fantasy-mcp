#!/usr/bin/env python3
"""Exporta la sesión de LaLigaApp a .env sin imprimir secretos."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from laliga_fantasy_mcp.auth.electron_reader import load_electron_tokens  # noqa: E402
from laliga_fantasy_mcp.config import DEFAULT_WEB_CLIENT_ID  # noqa: E402

ENV_PATH = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"


def _upsert(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    prefix = f"{key}="
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    return "\n".join(out) + "\n"


def main() -> int:
    tokens, origin = load_electron_tokens()
    if not tokens:
        print(
            "No se encontró sesión de LaLigaApp. Abre la app, inicia sesión "
            "y vuelve a ejecutar este script, o rellena .env a mano.",
            file=sys.stderr,
        )
        return 1
    access = tokens.get("access_token") or tokens.get("id_token") or ""
    refresh = tokens.get("refresh_token") or ""
    client_id = tokens.get("client_id") or DEFAULT_WEB_CLIENT_ID
    if ENV_PATH.exists():
        current = ENV_PATH.read_text(encoding="utf-8")
    elif EXAMPLE.exists():
        current = EXAMPLE.read_text(encoding="utf-8")
    else:
        current = ""
    current = _upsert(current, "LALIGA_FANTASY_TOKEN", access)
    current = _upsert(current, "LALIGA_FANTASY_REFRESH_TOKEN", refresh)
    current = _upsert(current, "LALIGA_FANTASY_CLIENT_ID", client_id)
    ENV_PATH.write_text(current, encoding="utf-8")
    print(
        f"Sesión exportada a .env (origen: {origin}; "
        f"access={'sí' if access else 'no'}; refresh={'sí' if refresh else 'no'})."
    )
    print("No copies .env al git ni lo pegues en chats.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
