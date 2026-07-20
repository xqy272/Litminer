"""MCP server fault-injection wrapper with a deliberately non-returning worker."""

from __future__ import annotations

import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from litminer.engine import run_lit_search
from litminer.sources.mcp import server


def _slow_run(_namespace):
    while True:
        time.sleep(0.25)


run_lit_search.run = _slow_run
server.main()
