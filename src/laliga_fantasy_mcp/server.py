"""Punto de entrada FastMCP (stdio)."""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from laliga_fantasy_mcp.tools import register_tools

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

mcp = FastMCP("laliga_fantasy_mcp")
register_tools(mcp)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
