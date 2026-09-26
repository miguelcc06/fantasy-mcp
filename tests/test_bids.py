"""Política de pujas, cliente de ofertas y tools de mercado."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from laliga_fantasy_mcp.client.api import FantasyAPIError, FantasyClient
from laliga_fantasy_mcp.config import get_bid_policy
from laliga_fantasy_mcp.models.schemas import (
    PlaceBidInput,
    ResponseFormat,
    TrimSoleBidsInput,
    UpdateBidInput,
)
from laliga_fantasy_mcp.services.bids import (
    bid_policy_error,
    classify_user_bid,
    plan_sole_bid_trims,
)
from laliga_fantasy_mcp.tools import (
    laliga_place_bid,
    laliga_trim_sole_bids,
    laliga_update_bid,
    register_tools,
)


def _listing(
    name: str,
    ptid: str,
    price: int,
    bids: int | None,
    user_bid: Any = None,
    pid: str | None = None,
    *,
    sale_price: int | None = None,
    market_value: int | None = None,
) -> dict[str, Any]:
    master: dict[str, Any] = {"id": pid or ptid, "nickname": name}
    if market_value is not None:
        master["marketValue"] = market_value
    item: dict[str, Any] = {
        "id": ptid,
        "playerTeamId": ptid,
        "discr": "marketPlayerTeam",
        "playerMaster": master,
        "salePrice": price if sale_price is None else sale_price,
    }
    if bids is not None:
        item["numberOfBids"] = bids
    if user_bid is not None:
        item["bid"] = user_bid if isinstance(user_bid, dict) else {"id": f"offer-{ptid}", "money": user_bid}
    return item


class FakeAPI:
    def __init__(self, items: list[Any] | None = None, *, fail_ids: set[str] | None = None) -> None:
        self.items = items or []
        self.fail_ids = fail_ids or set()
        self.placed: list[tuple[Any, ...]] = []
        self.updated: list[tuple[Any, ...]] = []
        self.market_calls = 0

    async def market(self, league_id: str) -> list[Any]:
        self.market_calls += 1
        self.league_id = league_id
        return self.items

    async def place_bid(self, league_id: str, player_team_id: str, amount: int) -> dict[str, Any]:
        self.placed.append((league_id, player_team_id, amount))
        return {"offer": amount, "playerTeamId": player_team_id}

    async def update_bid(
        self,
        league_id: str,
        player_team_id: str,
        amount: int,
        offer_id: str | None = None,
    ) -> dict[str, Any]:
        if player_team_id in self.fail_ids:
            raise FantasyAPIError("Error: fallo del servidor de LaLiga Fantasy.", 500)
        self.updated.append((league_id, player_team_id, amount, offer_id))
        return {"offer": amount, "id": offer_id}


def _bind(monkeypatch: pytest.MonkeyPatch, client: FakeAPI) -> FakeAPI:
    monkeypatch.setattr("laliga_fantasy_mcp.tools.api", lambda: client)
    return client


def test_bid_policy_default_and_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LALIGA_FANTASY_BID_POLICY", raising=False)
    assert get_bid_policy() == "update_own"
    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "  ")
    assert get_bid_policy() == "update_own"
    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "ReadOnly")
    assert get_bid_policy() == "readonly"
    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "CREATE_AND_UPDATE")
    assert get_bid_policy() == "create_and_update"
    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "yolo")
    with pytest.raises(ValueError, match="create_and_update"):
        get_bid_policy()


def test_bid_policy_blocks_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "readonly")
    assert bid_policy_error("place") and "readonly" in bid_policy_error("place")
    assert bid_policy_error("update") and "readonly" in bid_policy_error("update")
    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "update_own")
    place = bid_policy_error("place")
    assert place and "update_own" in place and "laliga_update_bid" in place
    assert bid_policy_error("update") is None
    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "create_and_update")
    assert bid_policy_error("place") is None
    assert bid_policy_error("update") is None
    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "nope")
    blocked = bid_policy_error("place")
    assert blocked and blocked.startswith("Error:")


def test_place_bid_schema_rejects_non_positive_amount() -> None:
    with pytest.raises(ValidationError):
        PlaceBidInput(player_team_id_or_name="Pedri", amount=0)
    with pytest.raises(ValidationError):
        UpdateBidInput(player_team_id_or_name="Pedri", amount=10, offer_id="a/b")


def test_classify_sole_bid_uses_sale_price_and_strict_minimum() -> None:
    high = _listing("Pedri", "55", 2_000_000, 1, 2_000_002, market_value=9_000_000)
    plan = classify_user_bid(high)
    assert plan is not None
    assert plan["action"] == "trim"
    assert plan["targetBid"] == 2_000_001
    assert plan["marketPrice"] == 2_000_000

    exact = _listing("Pedri", "55", 2_000_000, 1, 2_000_001)
    kept = classify_user_bid(exact)
    assert kept is not None and kept["action"] == "skipped"
    assert "no supera" in kept["reason"]

    below = _listing("Pedri", "55", 2_000_000, 1, 2_000_000)
    low = classify_user_bid(below)
    assert low is not None and low["action"] == "skipped"

    race = _listing("Pedri", "55", 2_000_000, 3, 9_000_000)
    competed = classify_user_bid(race)
    assert competed is not None and competed["action"] == "skipped"
    assert "competencia" in competed["reason"]

    other = _listing("Mbappé", "9", 5_000_000, 1, user_bid=None)
    assert classify_user_bid(other) is None

    raw_money = _listing("Vinicius", "7", 5_000_000, "1", user_bid=5_000_050)  # type: ignore[arg-type]
    raw_money["bid"] = 5_000_050
    raw = classify_user_bid(raw_money)
    assert raw is not None and raw["action"] == "trim" and raw["offerId"] is None
    assert raw["targetBid"] == 5_000_001


def test_plan_ignores_players_without_user_bid() -> None:
    items = [
        _listing("Solo", "1", 100, 1, 150),
        _listing("Rival", "2", 100, 1, None),
        _listing("Libre", "3", 100, 0, None),
        "basura",
    ]
    rows, without = plan_sole_bid_trims(items)
    assert without == 2
    assert [row["player"] for row in rows] == ["Solo"]


class _Tokens:
    async def ensure_fresh(self, _http: httpx.AsyncClient) -> str:
        return "tok"

    def bearer(self) -> str:
        return "tok"

    async def refresh(self, _http: httpx.AsyncClient) -> None:
        return None


def _http_client(handler: Any) -> FantasyClient:
    client = FantasyClient(_Tokens())  # type: ignore[arg-type]
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


@pytest.mark.asyncio
async def test_create_and_place_bid_post_contract() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "body": json.loads(request.content),
                "auth": request.headers.get("authorization"),
            }
        )
        return httpx.Response(201, json={"data": {"id": "offer-1", "offer": 1500000}})

    client = _http_client(handler)
    created = await client.create_bid("L9", "55", 1_500_000)
    placed = await client.place_bid("L9", "55", 1_500_000)
    await client.aclose()
    assert created == {"id": "offer-1", "offer": 1500000}
    assert placed == created
    assert len(captured) == 2
    for call in captured:
        assert call["method"] == "POST"
        assert call["auth"] == "Bearer tok"
        assert "/api/v1/competition/1/league/L9/playerTeam/55/offer" in call["url"]
        assert "/offer/" not in call["url"].split("offer", 1)[-1]
        assert "x-lang=es" in call["url"]
        assert call["body"] == {"offer": 1500000}


@pytest.mark.asyncio
async def test_update_and_modify_bid_put_contract() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({"method": request.method, "url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(200, json={"offer": json.loads(request.content)["offer"]})

    client = _http_client(handler)
    updated = await client.update_bid("L9", "55", 1_000_001, offer_id="77")
    modified = await client.modify_bid("L9", "55", 900_000)
    await client.aclose()
    assert updated == {"offer": 1_000_001}
    assert modified == {"offer": 900_000}
    assert captured[0]["method"] == "PUT"
    assert captured[0]["url"].endswith("/playerTeam/55/offer/77?x-lang=es") or (
        "/playerTeam/55/offer/77" in captured[0]["url"] and "x-lang=es" in captured[0]["url"]
    )
    assert captured[0]["body"] == {"offer": 1000001}
    assert captured[1]["method"] == "PUT"
    assert "/playerTeam/55/offer?" in captured[1]["url"] or captured[1]["url"].rstrip("/").endswith("/offer")
    assert "/offer/77" not in captured[1]["url"]
    assert captured[1]["body"] == {"offer": 900000}


@pytest.mark.asyncio
async def test_create_bid_does_not_retry_http_500() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"message": "Internal Server Error"})

    client = _http_client(handler)
    with pytest.raises(FantasyAPIError) as exc:
        await client.place_bid("L9", "55", 10)
    await client.aclose()
    assert calls["n"] == 1
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_place_bid_respects_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    market = [_listing("Pedri", "55", 1_000_000, 0)]
    client = _bind(monkeypatch, FakeAPI(market))

    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "readonly")
    readonly = await laliga_place_bid(PlaceBidInput(player_team_id_or_name="Pedri", amount=1_100_000, league_id="42"))
    assert readonly.startswith("Error:") and "readonly" in readonly
    assert client.placed == [] and client.market_calls == 0

    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "update_own")
    own = await laliga_place_bid(PlaceBidInput(player_team_id_or_name="Pedri", amount=1_100_000, league_id="42"))
    assert "update_own" in own and client.placed == [] and client.market_calls == 0

    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "create_and_update")
    placed = await laliga_place_bid(
        PlaceBidInput(
            player_team_id_or_name="Pedri",
            amount=1_100_000,
            league_id="42",
            response_format=ResponseFormat.JSON,
        )
    )
    body = json.loads(placed)
    assert body["status"] == "placed"
    assert body["playerTeamId"] == "55"
    assert body["amount"] == 1_100_000
    assert client.placed == [("42", "55", 1_100_000)]


@pytest.mark.asyncio
async def test_place_bid_rejects_unknown_ambiguous_and_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "create_and_update")
    items = [
        _listing("Juan Pérez", "1", 100, 0, pid="10"),
        _listing("Juan García", "2", 100, 0, pid="11"),
        _listing("Pedri", "55", 1_000_000, 1, 1_200_000),
        {
            "id": "free-1",
            "discr": "marketPlayerLeague",
            "playerMaster": {"id": "88", "nickname": "Suarez", "marketValue": 500_000},
            "numberOfBids": 0,
        },
    ]
    client = _bind(monkeypatch, FakeAPI(items))
    missing = await laliga_place_bid(PlaceBidInput(player_team_id_or_name="Zidane", amount=10, league_id="7"))
    assert "no está en el mercado" in missing
    ambiguous = await laliga_place_bid(PlaceBidInput(player_team_id_or_name="Juan", amount=10, league_id="7"))
    assert "varios jugadores" in ambiguous
    existing = await laliga_place_bid(PlaceBidInput(player_team_id_or_name="Pedri", amount=10, league_id="7"))
    assert "ya tienes una puja activa" in existing and "laliga_update_bid" in existing
    free = await laliga_place_bid(PlaceBidInput(player_team_id_or_name="Suarez", amount=10, league_id="7"))
    assert "playerTeamId" in free
    direct = await laliga_place_bid(PlaceBidInput(player_team_id_or_name="999", amount=25, league_id="7"))
    assert client.placed == [("7", "999", 25)]
    assert "Puja registrada" in direct


@pytest.mark.asyncio
async def test_update_bid_requires_own_active_bid(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        _listing("Pedri", "55", 1_000_000, 2, {"money": 1_500_000, "id": "offer-55"}),
        _listing("Gavi", "60", 800_000, 1, None),
    ]
    client = _bind(monkeypatch, FakeAPI(items))

    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "readonly")
    denied = await laliga_update_bid(UpdateBidInput(player_team_id_or_name="Pedri", amount=1_400_000, league_id="42"))
    assert "readonly" in denied and client.updated == [] and client.market_calls == 0

    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "update_own")
    absent = await laliga_update_bid(UpdateBidInput(player_team_id_or_name="Gavi", amount=900_000, league_id="42"))
    assert "no tienes una puja activa" in absent
    unknown = await laliga_update_bid(UpdateBidInput(player_team_id_or_name="999", amount=900_000, league_id="42"))
    assert "no hay una puja activa verificable" in unknown
    assert client.updated == []

    updated = await laliga_update_bid(
        UpdateBidInput(
            player_team_id_or_name="Pedri",
            amount=1_400_000,
            league_id="42",
            response_format=ResponseFormat.JSON,
        )
    )
    body = json.loads(updated)
    assert body["status"] == "updated"
    assert body["offerId"] == "offer-55"
    assert body["previousAmount"] == 1_500_000
    assert client.updated == [("42", "55", 1_400_000, "offer-55")]

    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "create_and_update")
    explicit = await laliga_update_bid(
        UpdateBidInput(player_team_id_or_name="55", amount=1_300_000, league_id="42", offer_id="custom-1")
    )
    assert "custom-1" in explicit
    assert client.updated[-1] == ("42", "55", 1_300_000, "custom-1")


@pytest.mark.asyncio
async def test_trim_sole_bids_dry_run_and_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "update_own")
    items = [
        _listing("Pedri", "55", 1_000_000, 1, {"money": 1_500_000, "id": "o-55"}),
        _listing("Gavi", "60", 800_000, 1, 800_001),
        _listing("Mbappé", "70", 5_000_000, 4, 6_000_000),
        _listing("Vinicius", "80", 4_000_000, 1, None),
        _listing("Yamal", "90", 3_000_000, 1, 3_000_002),
    ]
    client = _bind(monkeypatch, FakeAPI(items, fail_ids={"90"}))

    preview = await laliga_trim_sole_bids(
        TrimSoleBidsInput(dry_run=True, league_id="42", response_format=ResponseFormat.JSON)
    )
    planned = json.loads(preview)
    assert planned["dryRun"] is True
    assert planned["policy"] == "update_own"
    by_player = {row["player"]: row for row in planned["adjusted"]}
    assert by_player["Pedri"]["action"] == "would_trim"
    assert by_player["Pedri"]["targetBid"] == 1_000_001
    assert by_player["Yamal"]["targetBid"] == 3_000_001
    skipped = {row["player"]: row["reason"] for row in planned["skipped"]}
    assert "competencia" in skipped["Mbappé"]
    assert "no supera" in skipped["Gavi"]
    assert planned["summary"]["marketWithoutUserBid"] == 1
    assert client.updated == []
    assert "simulación" in (
        await laliga_trim_sole_bids(TrimSoleBidsInput(dry_run=True, league_id="42"))
    )

    applied = json.loads(
        await laliga_trim_sole_bids(
            TrimSoleBidsInput(dry_run=False, league_id="42", response_format=ResponseFormat.JSON)
        )
    )
    trimmed = {row["player"]: row for row in applied["adjusted"]}
    assert list(trimmed) == ["Pedri"]
    assert trimmed["Pedri"]["action"] == "trimmed"
    assert trimmed["Pedri"]["targetBid"] == 1_000_001
    failed = {row["player"]: row for row in applied["errors"]}
    assert failed["Yamal"]["action"] == "error"
    assert "fallo del servidor" in failed["Yamal"]["reason"]
    assert client.updated == [("42", "55", 1_000_001, "o-55")]
    assert all(call[1] != "70" for call in client.updated)
    assert all(call[1] != "80" for call in client.updated)


@pytest.mark.asyncio
async def test_trim_readonly_blocks_apply_but_allows_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LALIGA_FANTASY_BID_POLICY", "readonly")
    client = _bind(monkeypatch, FakeAPI([_listing("Pedri", "55", 100, 1, 500)]))
    blocked = await laliga_trim_sole_bids(TrimSoleBidsInput(dry_run=False, league_id="1"))
    assert blocked.startswith("Error:") and "readonly" in blocked
    assert client.market_calls == 0 and client.updated == []
    preview = json.loads(
        await laliga_trim_sole_bids(
            TrimSoleBidsInput(dry_run=True, league_id="1", response_format=ResponseFormat.JSON)
        )
    )
    assert preview["adjusted"][0]["action"] == "would_trim"
    assert preview["adjusted"][0]["targetBid"] == 101
    assert client.updated == []


def test_bid_tools_are_registered() -> None:
    class DummyMCP:
        def __init__(self) -> None:
            self.names: list[str] = []

        def tool(self, **kwargs: Any):
            def deco(fn: Any) -> Any:
                self.names.append(kwargs["name"])
                return fn

            return deco

    mcp = DummyMCP()
    register_tools(mcp)
    assert "laliga_place_bid" in mcp.names
    assert "laliga_update_bid" in mcp.names
    assert "laliga_trim_sole_bids" in mcp.names
