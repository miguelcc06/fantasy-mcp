"""Lectura de tokens persistidos por LaLigaApp (Electron)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

TOKEN_FILENAMES = ("laliga_auth_tokens.json", "laliga_tokens.json")
USERDATA_DIR_NAMES = (
    "LaLiga Fantasy App",
    "laliga-fantasy-app",
    "LaLigaApp",
    "laliga-fantasy-app-updater",
)


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    home = Path.home()
    for env_key in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        value = os.environ.get(env_key)
        if value:
            base = Path(value)
            dirs.extend(base / name for name in USERDATA_DIR_NAMES)
    dirs.append(home / "Library" / "Application Support")
    dirs.extend(
        (home / "Library" / "Application Support" / name) for name in USERDATA_DIR_NAMES
    )
    dirs.extend((home / ".config" / name) for name in USERDATA_DIR_NAMES)

    repo_root = Path(__file__).resolve().parents[3]
    portable = repo_root / "LaLigaApp"
    dirs.append(portable)
    dirs.append(portable / "userData")
    return dirs


def _parse_token_payload(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    tokens = raw.get("tokens") if isinstance(raw.get("tokens"), dict) else raw
    access = tokens.get("access_token") or tokens.get("id_token")
    if not access:
        return None
    return {
        "access_token": tokens.get("access_token") or tokens.get("id_token"),
        "id_token": tokens.get("id_token"),
        "refresh_token": tokens.get("refresh_token"),
        "token_type": tokens.get("token_type") or "Bearer",
        "expires_in": tokens.get("expires_in") or tokens.get("id_token_expires_in"),
        "expires_on": tokens.get("expires_on"),
        "client_id": tokens.get("client_id"),
    }


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        return _parse_token_payload(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def _scan_leveldb(directory: Path) -> dict[str, Any] | None:
    files = list(directory.glob("*.ldb")) + list(directory.glob("*.log"))
    files += [p for p in directory.iterdir() if p.is_file() and p.name.startswith("000")]
    token_re = re.compile(
        rb'\{[^{}]{0,200}"(?:access_token|id_token|refresh_token)"[^{}]{20,8000}\}'
    )
    for path in files:
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        for match in token_re.finditer(blob):
            try:
                text = match.group().decode("utf-8", errors="ignore")
            except Exception:
                continue
            parsed = _parse_token_payload(text)
            if parsed:
                return parsed
        # Chromium sometimes stores the JSON as a quoted localStorage string
        quoted = re.search(rb'laliga_tokens.{0,20}(\{[^{}]+access_token[^{}]+\})', blob)
        if quoted:
            try:
                text = quoted.group(1).decode("utf-8", errors="ignore")
                parsed = _parse_token_payload(text)
                if parsed:
                    return parsed
            except Exception:
                pass
    return None


def load_electron_tokens() -> tuple[dict[str, Any] | None, str | None]:
    """Devuelve (tokens, origen) o (None, None). No registra secretos."""
    seen: set[Path] = set()
    for directory in _candidate_dirs():
        try:
            directory = directory.resolve()
        except OSError:
            continue
        if directory in seen or not directory.exists():
            continue
        seen.add(directory)
        for name in TOKEN_FILENAMES:
            path = directory / name
            if path.is_file():
                parsed = _read_json_file(path)
                if parsed:
                    return parsed, str(path)
        local_storage = directory / "Local Storage" / "leveldb"
        if local_storage.is_dir():
            parsed = _scan_leveldb(local_storage)
            if parsed:
                return parsed, str(local_storage)
    return None, None
