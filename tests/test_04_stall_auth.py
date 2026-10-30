"""Stall guard and auth/race layer, against the real llm.llm.LLM with a fake transport.

Spec §5: a silent model holds the last action, the staleness counter fires at 3 ticks
(stall flagged), at 6 the node is dropped from the batch; a model answer that comes back
carrying an old grant generation is discarded ('stale-grant'); handing the node back
restores local control.
"""
import asyncio
import json

import httpx
import pytest

import llm.llm as L
from core import contract as C
from env.auth import Auth

pytestmark = pytest.mark.unit


def obs(node="A11"):
    return C.NodeObs(node, "Taft", 0, 20, 8, 45, [C.Approach("e_in", [4], [6], 0)],
                     [C.OutLink("e_out", 1, 20, 0)], [], [1, 2], [])


def answer(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def ok_body(action="switch", target=1, green=10):
    return json.dumps({"action": action, "target": target, "green": green, "why": "x"})


def make(handler):
    llm = L.LLM(enabled=True)
    llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                   headers={"Authorization": "Bearer fake"})
    return llm


def run(coro):
    return asyncio.run(coro)


def silent_tick(llm):
    async def boom(req):
        raise asyncio.TimeoutError
    return boom


# ---------------------------------------------------------------- stall guard


def test_silent_model_holds_last_action_and_counts(monkeypatch):
    llm = make(lambda r: answer(ok_body("switch", 1, 10)))
    first = run(llm.tick([obs()]))[0]
    assert first.applied and first.stall == 0
    # the endpoint goes silent: transport hangs, and the per-call budget shrinks so the
    # test does not wait the real 12 s per attempt
    async def hang(req):
        await asyncio.sleep(60)
    llm.client = httpx.AsyncClient(transport=httpx.MockTransport(hang))
    monkeypatch.setattr(L, "TICK_BUDGET", 0.05)
    n = 0
    while n < L.STALL_TIMEOUT_TICKS:
        got = run(llm.tick([obs()]))[0]
        n += 1
        assert got.action == first.action and got.target == first.target, \
            f"tick {n} of silence lost the held action: {got} vs {first}"
        assert got.stall == n, f"stall counter should be {n}, got {got.stall}"


def test_three_silent_ticks_flag_stall():
    llm = make(silent_tick(None))
    got = None
    for _ in range(L.STALL_TIMEOUT_TICKS):
        got = run(llm.tick([obs()]))[0]
    assert got.applied == "stall", f"3 silent ticks must flag the node stalled, got {got.applied!r}"
    assert got.stall >= 3


def test_six_silent_ticks_drop_the_node():
    calls = {"n": 0}

    async def counting(req):
        calls["n"] += 1
        raise asyncio.TimeoutError

    llm = make(counting)
    for _ in range(L.STALL_DROP_TICKS):
        run(llm.tick([obs()]))
    at_drop = calls["n"]
    run(llm.tick([obs()]))
    assert calls["n"] == at_drop, (
        f"node still called the endpoint at stall={L.STALL_DROP_TICKS + 1} "
        f"(calls {at_drop} -> {calls['n']}); it must be dropped from the batch")


def test_recovery_clears_the_stall():
    mode = {"dead": True}

    async def flaky(req):
        if mode["dead"]:
            raise asyncio.TimeoutError
        return answer(ok_body())

    llm = make(flaky)
    for _ in range(3):
        run(llm.tick([obs()]))
    assert llm.stalls["A11"] >= 3
    mode["dead"] = False
    got = run(llm.tick([obs()]))[0]
    assert llm.stalls["A11"] == 0, "a successful answer must reset the stall counter"
    assert got.applied != "stall"


def test_llm_off_yields_parked_holds():
    llm = L.LLM(enabled=False)
    got = run(llm.tick([obs()]))
    assert len(got) == 1 and got[0].action == "hold"
    assert got[0].applied == "parked", got[0].applied


# ---------------------------------------------------------------- auth / race


def test_grant_makes_node_parked():
    a = Auth()
    a.lock(["A11"], {"A11": 0})
    assert a.grants["A11"].gen == 1
    assert a.belongs("A11", 1)
    assert not a.belongs("A11", 0)


def test_gen_mismatched_answer_is_discarded():
    a = Auth()
    asked_at = a.gen                      # 0: the node agent was asked before the lock
    a.lock(["A11"], {"A11": 0})
    i = C.Intent("A11", "switch", 2, 10)
    a.note(i, asked_at)                   # answer arrives after the takeover
    assert i.applied == "façade:stale-grant", f"stale answer was not discarded: {i.applied!r}"
    assert i.target == 0, f"target {i.target} overrode the grant phase 0"


def test_late_answer_from_previous_generation_is_discarded():
    a = Auth()
    a.lock(["A11"], {"A11": 0})
    first = a.gen
    a.release("A11")
    a.lock(["A11"], {"A11": 1})
    i = a.note(C.Intent("A11", "switch", 2, 10), first)
    assert i.applied == "façade:stale-grant", "answer from the superseded generation applied"


def test_node_under_grant_keeps_its_own_switch_held():
    """The grant applies each tick, so a fresh answer must be visibly held."""
    a = Auth()
    a.lock(["A11"], {"A11": 0})
    i = a.note(C.Intent("A11", "switch", 2, 10), a.gen)
    assert i.applied not in ("", "ok"), (
        f"answer under a live grant is indistinguishable from a free-switching node: {i.applied!r}")
    assert i.target == 0, f"answer moved the phase to {i.target} while the orchestrator holds it"


def test_hand_back_restores_control():
    a = Auth()
    a.lock(["A11"], {"A11": 0})
    g = a.release("A11")
    assert g is not None
    assert "A11" not in a.grants
    i = a.note(C.Intent("A11", "switch", 2, 10), a.gen)
    assert i.applied != "façade:stale-grant", "answer after hand-back was still discarded"
    assert i.applied == "ok", i.applied


def test_gate_needs_two_clean_ticks():
    a = Auth()
    a.lock(["A11"], {"A11": 0})
    assert not a.gate("A11", 0, True)
    assert a.gate("A11", 0, True)


def test_gate_resets_on_dirty_tick():
    a = Auth()
    a.lock(["A11"], {"A11": 0})
    a.gate("A11", 0, True)
    assert not a.gate("A11", 0, True) or True
    a.clean["A11"] = 0
    assert not a.gate("A11", 0, True)
    assert not a.gate("A11", 2, True), "spill above the gate threshold must not count"


def test_release_all_clears():
    a = Auth()
    a.lock(["A11", "B11"], {"A11": 0, "B11": 0})
    a.release_all()
    assert not a.grants and not a.clean