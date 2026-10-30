"""LLM client over a fake HTTP transport: no network, no sleeps.

Spec: retry at most once per node per tick before the stall guard takes over; a tick
that times out yields a stall intent instead of raising; malformed JSON is retried
once and then stalled.
"""
import asyncio
import json

import httpx
import pytest

import llm.llm as L
from core import contract as C

pytestmark = pytest.mark.unit


def obs(node="A11"):
    return C.NodeObs(node, "Taft", 0, 20, 8, 45, [C.Approach("e_in", [4], [6], 0)],
                     [C.OutLink("e_out", 1, 20, 0)], [], [1, 2], [])


def run(coro):
    return asyncio.run(coro)


def make(handler):
    llm = L.LLM(enabled=True)
    llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                   headers={"Authorization": "Bearer fake"})
    return llm


def good(req):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
        {"action": "switch", "target": 1, "green": 10, "why": "q"})}}]})


def test_tick_returns_one_intent_per_node():
    llm = make(good)
    got = run(llm.tick([obs("A11"), obs("B11"), obs("C11")]))
    assert [i.node for i in got] == ["A11", "B11", "C11"]
    assert all(i.action == "switch" and i.applied != "stall" for i in got)


def test_schema_is_sent_strict():
    seen = {}

    def grab(req):
        seen.update(json.loads(req.content))
        return good(req)

    run(make(grab).tick([obs()]))
    rf = seen["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["properties"]["target"]["enum"] == [1, 2]


def test_timeout_yields_stall_intent_not_raise():
    async def slow(req):
        await asyncio.sleep(0.3)
    llm = make(slow)
    llm_budget = L.TICK_BUDGET
    L.TICK_BUDGET = 0.05                      # per-call budget, not a wall-clock sleep
    try:
        got = run(llm.tick([obs()]))
    finally:
        L.TICK_BUDGET = llm_budget
    assert len(got) == 1
    assert got[0].applied == "stall", f"timeout should yield a stall intent, got {got[0].applied!r}"
    assert got[0].action == "hold"


def test_malformed_json_is_retried_once_then_stalled():
    calls = {"n": 0}

    def junk(req):
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "{not json"}}]})

    got = run(make(junk).tick([obs()]))[0]
    assert calls["n"] == 2, f"malformed JSON should be retried exactly once, saw {calls['n']} calls"
    assert got.applied == "stall"


def test_malformed_then_good_recovers():
    seq = iter(["{bad", json.dumps({"action": "hold", "target": -1, "green": 10, "why": ""})])
    got = run(make(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": next(seq)}}]}))
              .tick([obs()]))[0]
    assert got.applied != "stall", "a good retry must not be thrown away"
    assert got.action == "hold"


def test_stall_reuses_previous_target():
    prev = None
    mode = {"ok": True}

    def flip(req):
        return good(req) if mode["ok"] else httpx.Response(500, json={"error": "x"})

    llm = make(flip)
    prev = run(llm.tick([obs()]))[0]
    mode["ok"] = False
    got = run(llm.tick([obs()]))[0]
    assert got.target == prev.target and got.green == prev.green, \
        f"stall lost the last action: {got} vs {prev}"


def test_orchestrate_failure_returns_empty_plan():
    llm = make(lambda r: httpx.Response(500, json={}))
    plan = run(llm.orchestrate("report"))
    assert plan["steps"] == [] and plan["release_order"] == []
    assert "unavailable" in plan["diagnosis"]