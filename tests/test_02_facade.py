"""Validity façade: what it must reject, hold, clamp and pass through.

Contract under test comes from SPEC §5 and the env loop: switch before min-green is
rejected and the node holds with reason 'early'; an out-of-table target is 'illegal';
a same-phase switch is a no-op; a requested green is clamped into [min, max].
"""
import inspect

import pytest

import llm.llm as L
from core import contract as C

pytestmark = pytest.mark.unit


def facade(o, i, last_switch=0, t=100):
    return "faça"  # placeholder, rebound below


facade = getattr(L, "faça", None) or getattr(L, "façade", None)


def sig(fn):
    return list(inspect.signature(fn).parameters)


def call(fn, o, i, **kw):
    """Call the façade with whichever arguments its signature takes.

    Timing may arrive as parameters (last_switch, t) or on the observation; the
    explicit kwargs win so each test can pin the exact second.
    """
    names = sig(fn)
    args = {"obs": o, "o": o, "intent": i, "i": i,
            "last_switch": kw.get("last_switch", getattr(o, "last_switch", 0)),
            "t": kw.get("t", getattr(o, "t", 100)), "node": o.node,
            "phase_pressure": kw.get("phase_pressure", {})}
    return fn(*[args[n] for n in names])


def test_contract_carries_timing_for_the_facade():
    """The façade needs last_switch and t; they must live on the observation."""
    fields = C.NodeObs.__dataclass_fields__
    assert "last_switch" in fields and "t" in fields


def reason(i):
    return i.applied.split(":", 1)[1] if i.applied.startswith("façade:") else i.applied


def test_facade_exists():
    assert callable(facade), "llm.llm must expose the façade (llm.llm.faça)"


def test_early_switch_is_rejected_and_holds():
    o = C.NodeObs("A11", "Taft", 0, 3, 8, 45, [C.Approach("e_in", [4], [6], 0)],
                  [C.OutLink("e_out", 1, 20, 0)], [], [1, 2], [])          # in_green=3 < 8
    i = call(facade, o, C.Intent("A11", "switch", 1, 15))
    assert i.action == "hold", f"a switch at in_green=3 was applied: {i}"
    assert reason(i) == "early", f"expected reason 'early', got {i.applied!r}"


def test_illegal_target_is_rejected():
    o = C.NodeObs("A11", "Taft", 0, 20, 8, 45, [C.Approach("e_in", [4], [6], 0)],
                  [C.OutLink("e_out", 1, 20, 0)], [], [1, 2], [])          # 9 not in table
    i = call(facade, o, C.Intent("A11", "switch", 9, 15))
    assert reason(i) == "illegal", f"phase 9 outside the table was not flagged illegal: {i}"
    assert i.target in (1, 2), f"illegal target left {i.target} instead of a legal one"


def test_same_phase_switch_is_noop():
    o = C.NodeObs("A11", "Taft", 0, 20, 8, 45, [C.Approach("e_in", [4], [6], 0)],
                  [C.OutLink("e_out", 1, 20, 0)], [], [1, 2], [])
    i = call(facade, o, C.Intent("A11", "switch", 0, 15))
    assert i.target == 0
    assert i.action == "hold" or i.applied.startswith("façade"), \
        f"switching to the current phase must not change it: {i}"
    assert i.applied != "ok" or i.action == "hold"


def test_green_is_clamped_into_range():
    o_short = C.NodeObs("A11", "Taft", 0, 20, 8, 45, [C.Approach("e_in", [4], [6], 0)],
                        [C.OutLink("e_out", 1, 20, 0)], [], [1, 2], [])
    lo = call(facade, o_short, C.Intent("A11", "switch", 1, 3))
    assert lo.green >= 8, f"green 3 not clamped up to min_green 8: {lo.green}"
    o = C.NodeObs("A11", "Taft", 0, 20, 8, 45, [C.Approach("e_in", [4], [6], 0)],
                  [C.OutLink("e_out", 1, 20, 0)], [], [1, 2], [])
    hi = call(facade, o, C.Intent("A11", "hold", -1, 90))
    assert hi.green <= 45, f"green 90 not clamped down to max_green 45: {hi.green}"


def test_legal_switch_after_min_green_passes():
    o = C.NodeObs("A11", "Taft", 0, 20, 8, 45, [C.Approach("e_in", [4], [6], 0)],
                  [C.OutLink("e_out", 1, 20, 0)], [], [1, 2], [])
    i = call(facade, o, C.Intent("A11", "switch", 1, 15), last_switch=10, t=100)
    assert i.action == "switch" and i.target == 1 and i.applied == "ok", i


def test_garbage_duration_does_not_raise():
    o = C.NodeObs("A11", "Taft", 0, 20, 8, 45, [C.Approach("e_in", [4], [6], 0)],
                  [C.OutLink("e_out", 1, 20, 0)], [], [1, 2], [])
    i = call(facade, o, C.Intent("A11", "hold", -1, None))
    assert 8 <= i.green <= 45, i.green