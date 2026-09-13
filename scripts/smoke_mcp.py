"""Smoke test MCP stdio: lista tools y llama onces/lesiones (sin token)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


async def main() -> None:
    params = StdioServerParameters(
        command=str(PYTHON),
        args=["-m", "laliga_fantasy_mcp"],
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print("tools", len(names))
            assert len(names) == 19, names
            result = await session.call_tool(
                "laliga_get_probable_lineups",
                {"team_or_match": "real-madrid"},
            )
            text = result.content[0].text if result.content else ""
            print(text[:400])
            assert "Real Madrid" in text or "real-madrid" in text.lower()


if __name__ == "__main__":
    asyncio.run(main())
