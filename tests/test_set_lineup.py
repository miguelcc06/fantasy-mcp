import json
from typing import Any

import httpx
import pytest

from laliga_fantasy_mcp.client.api import FantasyAPIError, FantasyClient
from laliga_fantasy_mcp.tools import (
    build_lineup_put_payload,
    lineup_player_team_id,
    parse_tactical_formation,
    prepare_set_lineup_payload,
)


def _player(ptid: str, pid: str, name: str, position_id: int) -> dict[str, Any]:
    return {
        "id": ptid,
        "playerTeamId": ptid,
        "playerMaster": {"id": pid, "name": name, "nickname": name, "positionId": position_id},
    }


def _roster_442() -> list[dict[str, Any]]:
    players = [_player("gk1", "101", "Courtois", 1)]
    players.extend(_player(f"df{i}", str(200 + i), f"Def{i}", 2) for i in range(4))
    players.extend(_player(f"mf{i}", str(300 + i), f"Mid{i}", 3) for i in range(4))
    players.extend(_player(f"st{i}", str(400 + i), f"Str{i}", 4) for i in range(2))
    return players


def _starters_442() -> list[str]:
    return ["Courtois", "Def0", "Def1", "Def2", "Def3", "Mid0", "Mid1", "Mid2", "Mid3", "Str0", "Str1"]


def test_parse_tactical_formation_variants() -> None:
    assert parse_tactical_formation("4-4-2") == (4, 4, 2)
    assert parse_tactical_formation("3,5,2") == (3, 5, 2)
    assert parse_tactical_formation("1-4-3-3") == (4, 3, 3)
    assert parse_tactical_formation("nope") is None


def test_lineup_player_team_id_ignores_catalog_id() -> None:
    item = {
        "id": "slot-9",
        "playerTeamId": "slot-9",
        "playerMaster": {"id": "1197", "name": "Kubo"},
    }
    assert lineup_player_team_id(item) == "slot-9"
    catalog_only = {"id": "1197", "name": "Kubo", "playerMaster": {"id": "1197"}}
    assert lineup_player_team_id(catalog_only) is None
    fallback = {"id": "slot-1", "playerMaster": {"id": "50", "name": "X"}}
    assert lineup_player_team_id(fallback) == "slot-1"


def test_put_payload_is_flat_ids_not_get_wrapper() -> None:
    payload = build_lineup_put_payload(
        goalkeeper=["11"],
        defender=["21", "22", "23", "24"],
        midfield=["31", "32", "33", "34"],
        striker=["41", "42"],
        formation=(4, 4, 2),
    )
    assert payload == {
        "goalkeeper": 11,
        "defender": [21, 22, 23, 24],
        "midfield": [31, 32, 33, 34],
        "striker": [41, 42],
        "tactical_formation": [4, 4, 2],
    }
    assert "formation" not in payload
    assert "tacticalFormation" not in payload
    assert "coach" not in payload
    assert "bench" not in payload
    assert "captain" not in payload


def test_put_payload_optional_premium_fields() -> None:
    payload = build_lineup_put_payload(
        goalkeeper=["gk"],
        defender=["d1"],
        midfield=["m1"],
        striker=["s1"],
        formation=(1, 1, 1),
        captain_id="m1",
        bench_ids=["b1"],
        coach_id="c1",
    )
    assert payload["captain"] == "m1"
    assert payload["bench"] == ["b1"]
    assert payload["coach"] == "c1"
    assert payload["goalkeeper"] == "gk"


def test_prepare_valid_442() -> None:
    payload, starters, error = prepare_set_lineup_payload(
        _roster_442(), "4-4-2", _starters_442()
    )
    assert error is None
    assert payload is not None
    assert len(starters) == 11
    assert payload["tactical_formation"] == [4, 4, 2]
    assert payload["goalkeeper"] == "gk1"
    assert payload["defender"] == ["df0", "df1", "df2", "df3"]
    assert "formation" not in payload


def test_prepare_rejects_unknown_and_wrong_shape() -> None:
    _, _, missing = prepare_set_lineup_payload(_roster_442(), "4-4-2", ["Nadie"] + _starters_442()[1:])
    assert missing and "no pertenece" in missing
    names = ["Courtois"] + [f"Def{i}" for i in range(4)] + [f"Mid{i}" for i in range(5)] + ["Str0"]
    _, _, shape = prepare_set_lineup_payload(_roster_442() + [_player("mf4", "304", "Mid4", 3)], "4-4-2", names)
    assert shape and "centrocampistas" in shape
    _, _, dup = prepare_set_lineup_payload(
        _roster_442(),
        "4-4-2",
        ["Courtois", "Courtois"] + _starters_442()[2:],
    )
    assert dup and "duplicado" in dup


def test_prepare_captain_must_be_starter() -> None:
    extra = _roster_442() + [_player("gk2", "102", "Lunin", 1)]
    _, _, err = prepare_set_lineup_payload(extra, "4-4-2", _starters_442(), captain_id="Lunin")
    assert err and "titulares" in err
    payload, _, error = prepare_set_lineup_payload(
        _roster_442(), "4-4-2", _starters_442(), captain_id="Mid0"
    )
    assert error is None
    assert payload is not None
    assert payload["captain"] == "mf0"


class _Tokens:
    async def ensure_fresh(self, _http: httpx.AsyncClient) -> str:
        return "tok"

    def bearer(self) -> str:
        return "tok"

    async def refresh(self, _http: httpx.AsyncClient) -> None:
        return None


def _client(handler: Any) -> FantasyClient:
    client = FantasyClient(_Tokens())  # type: ignore[arg-type]
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


@pytest.mark.asyncio
async def test_set_lineup_put_path_and_query() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    payload = build_lineup_put_payload(
        goalkeeper=["1"],
        defender=["2", "3", "4"],
        midfield=["5", "6", "7"],
        striker=["8", "9", "10"],
        formation=(3, 3, 3),
    )
    result = await client.set_lineup("team-99", payload)
    await client.aclose()
    assert result == {"ok": True}
    assert captured["method"] == "PUT"
    assert "/api/v1/competition/1/teams/team-99/lineup" in captured["url"]
    assert "x-lang=es" in captured["url"]
    assert captured["body"]["tactical_formation"] == [3, 3, 3]
    assert "formation" not in captured["body"]


@pytest.mark.asyncio
async def test_set_lineup_retries_without_premium_extras() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "captain" in body:
            return httpx.Response(500, json={"code": 500, "message": "Internal Server Error"})
        return httpx.Response(204)

    client = _client(handler)
    payload = build_lineup_put_payload(
        goalkeeper=["1"],
        defender=["2"],
        midfield=["3"],
        striker=["4"],
        formation=(1, 1, 1),
        captain_id="3",
    )
    result = await client.set_lineup("t1", payload)
    await client.aclose()
    assert result is None
    assert len(bodies) == 2
    assert "captain" in bodies[0]
    assert "captain" not in bodies[1]


@pytest.mark.asyncio
async def test_set_lineup_does_not_retry_plain_500() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"code": 500, "message": "Internal Server Error"})

    client = _client(handler)
    payload = build_lineup_put_payload(
        goalkeeper=["1"],
        defender=["2"],
        midfield=["3"],
        striker=["4"],
        formation=(1, 1, 1),
    )
    with pytest.raises(FantasyAPIError) as exc:
        await client.set_lineup("t1", payload)
    await client.aclose()
    assert exc.value.status_code == 500
    assert calls["n"] == 1
    assert "fallo del servidor" in str(exc.value)
