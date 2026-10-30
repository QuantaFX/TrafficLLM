"""Phase tables per junction, built from the connection set.

A phase is one (opposing-approach pair, turn-class) group: throughs of both opposing
approaches, then their lefts. A junction with 210 raw links collapses to a handful of
groups this way, which keeps the phase index small enough to put in a schema.
"""
import math

from core.contract import Phase

_CACHE: dict[tuple, list[Phase]] = {}
# SUMO's connection `dir` is relative to the geometric heading: s = straight, L = left,
# R = right (t is only emitted on some nets). Anyone reading `t` alone sees almost no
# through movements and the phase table collapses to single approaches.
CLASS = {"l": "left", "L": "left", "t": "through", "s": "through",
         "r": "right", "R": "right", "T": "through"}


def build(frm: list[str], to: list[str], direction: list[str],
          pos_of: dict[str, tuple[float, float]] | None = None) -> list[Phase]:
    pos_of = pos_of or {}
    key = (tuple(zip(frm, to, direction)), tuple(sorted(pos_of.items())))
    if key in _CACHE:
        return _CACHE[key]
    n = len(frm)
    approaches: dict[str, list[int]] = {}
    for i, f in enumerate(frm):
        approaches.setdefault(f, []).append(i)

    def _keys(idxs):
        """Movement keys carrying the real out-edge, so pressure can join on it."""
        return [f"{CLASS.get(direction[i], 'through')}:{frm[i]}" for i in idxs]

    def sel(idxs, cls):
        return [i for i in idxs if CLASS.get(direction[i]) == cls]

    # Two approaches oppose when they arrive on the same street from opposite sides,
    # i.e. their bearing into the junction differs by ~180 deg. Pairing on a shared
    # destination edge fails at boundary arms (a dead-end arm shares none) and at
    # 8-approach nodes where three streets converge.
    pos = {f: np for f in approaches for np in [pos_of.get(f)]}
    bearing = {}
    for f in approaches:
        q, j = pos.get(f), pos.get("@")
        if q and j:
            bearing[f] = math.atan2(q[1] - j[1], q[0] - j[0])
    # Bucket by rounded bearing so each street is paired with itself, never across
    # streets: at an 8-approach node three streets converge and a global "nearest
    # partner" search would chain them into one mega-phase.
    buckets: dict[int, list[str]] = {}
    for f in approaches:
        r = round(bearing.get(f, 0.0) / (math.pi / 2))
        buckets.setdefault(r % 4, []).append(f)
    pairs = []
    for b in (0, 2, 1, 3):               # N-S first, then E-W, then the diagonals
        fr = buckets.get(b, [])
        fr = sorted(fr, key=lambda f: bearing.get(f, 0.0))
        while len(fr) >= 2:
            a1, b1 = fr.pop(0), fr.pop(0)
            pairs.append((a1, b1))
        for f in fr:
            pairs.append((f, None))

    groups: list[tuple[str, list[int], list[str]]] = []
    for a, b in pairs:
        both = approaches[a] + (approaches[b] if b else [])
        # One phase per street pair: throughs, rights AND lefts together. Splitting the
        # left turn out doubles the cycle (8 groups -> 16) and starves left-only lanes.
        t_ = sel(both, "through") + sel(both, "right")
        l_ = sel(both, "left")
        if t_ or l_:
            groups.append(("through", t_ + l_, _keys(t_ + l_)))

    out: list[Phase] = []

    def add(kind, idxs, labels):
        s = ["r"] * n
        for i in idxs:
            s[i] = "G"
        out.append(Phase(len(out), "".join(s), tuple(labels), kind))

    for kind, idxs, labels in groups:
        add(kind, idxs, labels)
    if not out:
        add("through", list(range(n)), _keys(list(range(n))))
    for i in range(n):                       # escape phases: one connection each
        add("escape", [i], _keys([i]))
    out.append(Phase(len(out), "r" * n, (), "allred"))
    _CACHE[key] = out
    return out


def table_for(_tls: str, links: list[dict], pos_of: dict[str, tuple[float, float]] | None = None) -> list[Phase]:
    return build([l["frm"] for l in links], [l["to"] for l in links],
                 [l["direction"] for l in links], pos_of)