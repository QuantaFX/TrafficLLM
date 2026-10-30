"""Metrics: blocked-edge count and the cycle finder, on hand-built tiny nets.

The detector is the trigger for everything the orchestrator does, so it must be
neither blind (no blocked edges under saturation) nor trigger-happy (a cycle from a
chain, or a 2-node ring).
"""
import pytest

from env.env import Env

pytestmark = pytest.mark.unit


def build_env(topology, blocked, spill=None):
    """topology: {node: [out_edges]} ; blocked: set of edges at >=0.9 occupancy."""
    e = Env()
    e.started = True
    spill = spill or {}
    for n, outs in topology.items():
        e.nodes[n] = {"id": n, "name": n, "links": [], "approaches": [], "outgoing": outs,
                      "neighbours": []}
    e._out = lambda edge: _Out(edge, 2 if edge in blocked else 0)
    return e


class _Out:
    def __init__(self, edge, spill):
        self.edge, self.spillback = edge, spill


def test_no_blockage_no_cycle():
    e = build_env({"A": ["e_A_B"], "B": ["e_B_C"], "C": ["e_C_A"]}, set())
    assert e.blocked_edges() == []
    assert e.cycle() == []


def test_three_ring_is_found():
    topo = {"A": ["e_A_B"], "B": ["e_B_C"], "C": ["e_C_A"]}
    e = build_env(topo, {"e_A_B", "e_B_C", "e_C_A"})
    blocked = {frozenset(x) for x in e.blocked_edges()}
    assert blocked == {frozenset(("A", "B")), frozenset(("B", "C")), frozenset(("C", "A"))}, \
        f"blocked edges reported in the wrong shape: {e.blocked_edges()}"
    cyc = e.cycle()
    assert set(cyc) == {"A", "B", "C"} and len(cyc) == 3, cyc


def test_blocked_count_matches_edges_not_nodes():
    topo = {"A": ["e_A_B", "e_A_C"], "B": ["e_B_A"], "C": ["e_C_A"]}
    e = build_env(topo, {"e_A_B", "e_A_C", "e_B_A", "e_C_A"})
    assert len(e.blocked_edges()) == 4


def test_two_node_ring_is_not_reported_as_cycle():
    topo = {"A": ["e_A_B"], "B": ["e_B_A"]}
    e = build_env(topo, {"e_A_B", "e_B_A"})
    assert len(e.blocked_edges()) == 2
    assert e.cycle() == [], f"a 2-node ring reported as a district cycle: {e.cycle()}"


def test_open_chain_has_no_cycle():
    topo = {"A": ["e_A_B"], "B": ["e_B_C"], "C": ["e_C_D"], "D": []}
    e = build_env(topo, {"e_A_B", "e_B_C", "e_C_D"})
    assert e.cycle() == []


def test_producer_consumer_edges_count_as_blocked():
    """Real net: an out-arc with >=0.9 occupancy must show up in the stats counter."""
    e = Env()
    e.started = True
    e.nodes = {"A11": {"id": "A11", "name": "n", "links": [], "approaches": ["e_in"],
                       "outgoing": ["e_out"], "neighbours": []}}
    e._lane_stats = lambda edge: ([10], [0])
    e._out = lambda edge: _Out(edge, 2)
    assert e.stats()["blocked"] == 1