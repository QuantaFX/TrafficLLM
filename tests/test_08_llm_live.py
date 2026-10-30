"""Optional live test against the real endpoint. Opt in with `-m live`; skipped by default.

Loads the key from .env when the environment has none. No sleeps, one batched tick.
"""
import asyncio
import os

import pytest

pytestmark = pytest.mark.live

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def key():
    if os.environ.get("LLM_KEY"):
        return os.environ["LLM_KEY"]
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            name, _, val = line.strip().partition("=")
            if name in ("LLM_KEY", "HERMES_CUSTOM_PANDEV_API_KEY") and val:
                return val.strip().strip('"')
    return ""


@pytest.mark.skipif(not key(), reason="no LLM_KEY / HERMES_CUSTOM_PANDEV_API_KEY")
def test_live_tick_is_schema_valid():
    import llm.llm as L
    from core import contract as C

    os.environ["LLM_KEY"] = key()
    import importlib
    importlib.reload(L)
    llm = L.LLM(enabled=True)

    o = C.NodeObs("A11", "Taft Ave & Vito Cruz", 0, 20, 8, 45,
                  [C.Approach("e_in", [6], [12], 0)], [C.OutLink("e_out", 2, 30, 1)],
                  [{"node": "B11", "name": "Vito Cruz & Apacible", "phase": 1, "queue": 5,
                    "spillback": 1}], [1, 2], [])

    async def one_tick():
        try:
            return await llm.tick([o])     # aclose in the same loop, or the client dies with it
        finally:
            await llm.aclose()

    i = asyncio.run(one_tick())[0]
    assert i.node == "A11"
    assert i.action in C.ACTIONS
    if i.applied != "stall":
        assert i.target in (0, 1, 2)
        assert i.green > 0