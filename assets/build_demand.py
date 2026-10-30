"""Generate turn-heavy, platoon-like demand for the 3x3 Taft grid.

Why not uniform per-boundary inflow: uniform demand spreads vehicles evenly, queues
never cohere, and the demo never reaches gridlock. This generator instead

  * releases vehicles in *bursts* (a leader plus a tight tail), so platoons arrive at a
    signal together and spill back through the next junction;
  * routes each one by a random forward walk weighted toward turns (left 2.5 : right 1.8
    : straight 1.0), so a platoon that turns leaves the through lane starved and blocks
    the left lane behind it;
  * injects extra long southbound platoons on the Taft axis (the approach that has to
    cross the most junction cycles);
  * staggers departure over time (vType with `--route-steps`, a randomized depart time,
    and a Poisson gap between platoons) instead of dumping every vehicle at t=0;
  * varies departLane / departSpeed / departPos per vehicle, mixing in a minority that
    enter at the stop line (departPos=last) and a minority at speed (departSpeed=max),
    so insertion itself is a stress case rather than a clean fill.

Sources are discovered from the net: an entry edge is one whose FROM junction is a
perimeter arm junction. Nothing here hardcodes edge ids, so it survives renaming.

Everything is drawn from one --seed, so a given seed reproduces byte-identically.

    python assets/build_demand.py --seed 7 --scenario platoon
    python assets/build_demand.py --scenario gridlock --scale 1.6 --out assets/demand.rou.xml

Scenarios live in assets/scenarios.json; the sim reads the same file for the knobs
(route multiplier, veh rate) recorded in the header comment of the generated file.
"""
import argparse
import json
import os
import random
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
DEFAULT_NET = os.path.join(ASSETS, "taft.net.xml")

# Forward-walk weights per SUMO connection dir. Turns dominate; U-turns are allowed
# but rare because they only exist where a stub arm has both directions.
WALK_W = {"l": 2.5, "r": 1.8, "s": 1.0, "L": 0.35, "R": 0.7, "t": 1.0}

# Departure variety weights (the task fixes the value sets, not the frequencies).
LANE_W = {"best": 0.45, "free": 0.35, "random": 0.20}
SPEED_W = {"max": 0.5, "7": 0.5}
POS_W = {"free": 0.55, "random_free": 0.25, "base": 0.12, "last": 0.08}

VTYPES = [
    # id,        length, accel, decel, wanted speed, gap, sigma
    ("car",       4.5, 2.6, 4.5, 13.9, 2.0, 0.5),
    ("taxi",      4.7, 2.8, 4.8, 15.0, 1.6, 0.6),
    ("jeepney",   6.4, 1.9, 3.8, 12.0, 2.2, 0.4),
    ("truck",     8.5, 1.3, 3.2, 11.0, 2.6, 0.3),
]
VTYPE_W = [0.62, 0.18, 0.14, 0.06]

# Edges at least this long take the full departure mix.
LONG_EDGE = 60.0


# ---------------------------------------------------------------- net discovery ----
def load_net(path):
    """Return (edges, entry_edges, southbound_entry_edges, warnings).

    Perimeter nodes are `priority`/`dead_end`, but so are the arm *ends*, so type alone
    is not enough: a node is a perimeter stop only if it left a junction reachable from
    the signalised core by a single edge. Anything outside the core box counts.
    """
    root = ET.parse(path).getroot()
    warn = []
    js = list(root.iter("junction"))
    jtype = {j.get("id"): j.get("type", "") for j in js}
    pos = {j.get("id"): (float(j.get("x", 0)), float(j.get("y", 0))) for j in js}
    sig = {j for j, t in jtype.items() if t not in ("priority", "dead_end", "internal")}
    # Grid nodes carry no arm suffix and are what a by-hand build always names
    # (A00..C22). Anchor on them so this survives any junction type or signal scheme.
    grid = {j for j in jtype if "_" not in j and j in pos}
    if not grid:
        grid = set(sig)
    if not grid:
        raise SystemExit(f"{path}: cannot find any grid junctions")

    by_from = {}
    for c in root.iter("connection"):
        by_from.setdefault(c.get("from"), []).append(c)

    edges = {}
    for e in root.iter("edge"):
        if e.get("function") == "internal":
            continue
        eid, fr, to = e.get("id"), e.get("from"), e.get("to")
        if fr is None or to is None:
            # id convention is "e<from>_<to>"; node ids may themselves contain "_",
            # so match the longest known junction id rather than splitting blindly.
            body = eid[1:] if eid.startswith("e") else eid
            cuts = [(body[: i], body[i + 1:]) for i, ch in enumerate(body)
                    if ch == "_" and body[:i] in jtype and body[i + 1:] in jtype]
            if not cuts:
                warn.append(f"cannot resolve endpoints of edge {eid}")
                continue
            fr, to = cuts[0]
        conns = {}
        for c in list(e.iter("connection")) + by_from.get(eid, []):
            conns.setdefault(c.get("fromLane"), []).append(
                (c.get("to"), c.get("toLane"), c.get("dir", "s")))
        lanes = list(e.iter("lane"))
        ln = float(e.get("length") or max((float(l.get("length", 0)) for l in lanes), default=0))
        edges[eid] = {"to": to, "from": fr, "conns": conns, "len": ln}

    # Boundary arms are the "<node>_<N|S|E|W>" perimeter junctions. Prefer that
    # convention; fall back to any node one hop off the grid core.
    arms = {j for j in jtype if j.rsplit("_", 1)[-1] in ("N", "S", "E", "W") and "_" in j}
    if arms:
        perimeter = arms
    else:
        warn.append("no *_N/_S/_E/_W arm junctions; falling back to off-core topology")
        neighbours = {n for e in edges.values() for n in (e["from"], e["to"])}
        perimeter = {n for n in neighbours if n not in grid}
    # Arms are meshed too, so leaving an arm can land on a grid node (the real feed
    # stub) or on a neighbouring arm (a short turn stub). Only feed stubs carry the
    # through traffic; the turn stubs are optional.
    raw = sorted(e for e in edges if edges[e]["from"] in perimeter)
    entry = [e for e in raw if edges[e]["to"] in grid]
    turnys = [e for e in raw if edges[e]["to"] not in grid]
    if not entry:
        warn.append("no arm-to-grid feed stubs; using every off-arm edge")
        entry = raw
    # southbound = arm north of the grid (Taft traffic feeds in from the top)
    y_top = max(pos[j][1] for j in grid)
    south = [e for e in entry if pos.get(edges[e]["from"], (0, 0))[1] > y_top]
    warn = sorted(set(warn))
    if not entry:
        raise SystemExit(f"{path}: no boundary entry edges found")
    if not south:
        warn.append("no north-arm approach detected; Taft platoons fall back to any entry")
        south = entry
        warn = sorted(set(warn))
    return edges, entry, south, turnys, perimeter, warn


def edge_len(e):
    return e.get("len") or 0.0


def walk(edges, rng, src, hops, hubs=None, avoid=0.8):
    """Random forward walk from src, weighted toward turns. Returns list of edge ids."""
    route, cur, seen = [src], src, {src}
    for _ in range(hops):
        outs = edges[cur]["conns"]
        cand = [(to, w) for ln, lst in outs.items() for (to, _tl, d) in lst
                for w in [WALK_W.get(d, 1.0)]
                if to in edges and to not in seen]
        if not cand:
            break
        if hubs:
            cand = [(t2, wt * (avoid if edges[t2]["from"] in hubs else 1.0)) for t2, wt in cand]
        nxt = rng.choices([c[0] for c in cand], [c[1] for c in cand])[0]
        route.append(nxt)
        seen.add(nxt)
        cur = nxt
    return route


def depart_attrs(rng, length):
    """Departure variety, restricted to what the edge can actually do.

    SUMO refuses departSpeed=7 at departPos=base when the edge is too short to slow
    down before the junction ("junction too close"), and `last` needs room behind the
    stop line too. So: on long edges use the full mix; on short ones drop those.
    """
    if length >= LONG_EDGE:
        pos, speed = _pick(rng, POS_W), _pick(rng, SPEED_W)
    else:
        pos = rng.choices(["free", "random_free"], [0.8, 0.2])[0]
        speed = rng.choices(["max", "0"], [0.6, 0.4])[0]
    if speed != "max":
        pos = rng.choices(["free", "base"], [0.85, 0.15])[0] if length >= LONG_EDGE else "free"
    return {"departLane": _pick(rng, LANE_W), "departSpeed": speed, "departPos": pos}


def veh_id(n):
    # Stable, sortable, unique across speed-warped and burst generators.
    return f"v{n:06d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", default=DEFAULT_NET)
    ap.add_argument("--out", default=os.path.join(ASSETS, "demand.rou.xml"))
    ap.add_argument("--scenarios", default=os.path.join(ASSETS, "scenarios.json"))
    ap.add_argument("--scenario", default="baseline")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--scale", type=float, default=None,
                    help="override scenario scale (veh rate and, if flagged, platoon length)")
    ap.add_argument("--veh", type=float, default=None,
                    help="override VEHICLES PER HOUR OBSERVED (total, not per approach)")
    ap.add_argument("--end", type=float, default=None, help="override demand end time")
    ap.add_argument("--hops", default=None,
                    help="min-max hops per route (default: the scenario's own value)")
    ap.add_argument("--avoid-hubs", type=float, default=1.0,
                    help="weight multiplier on arm-junction turns; <1 discourages "
                                             "revisiting the uncontrolled perimeter arms")
    ap.add_argument("--max", type=int, default=40000, help="hard cap on vehicles written")
    ap.add_argument("--no-platoons", action="store_true", help="pure Poisson release")
    ap.add_argument("--route-steps", type=float, default=0.0,
                    help="also sorted by depart; echoes the SUMO load granularity")
    a = ap.parse_args()

    with open(a.scenarios) as f:
        scen = json.load(f)["scenarios"]
    if a.scenario not in scen:
        raise SystemExit(f"unknown scenario {a.scenario!r}; have {sorted(scen)}")
    s = dict(scen[a.scenario])
    scale = a.scale if a.scale is not None else s.get("scale", 1.0)
    end = a.end if a.end is not None else s.get("end_s", 3600)
    target = a.veh if a.veh is not None else s.get("veh_h", 4000) * scale
    burst_mult = s.get("burst_mult", 1.0) * (scale if s.get("scale_bursts") else 1.0)
    lo_h, hi_h = (int(v) for v in (a.hops or s.get("hops", "2-4")).split("-"))

    rng = random.Random(a.seed)
    edges, entry, south, turnstubs, arm_junctions, warn = load_net(a.net)
    hubs = arm_junctions if a.avoid_hubs != 1.0 else None
    for w in warn:
        print(f"warn: {w}", file=sys.stderr)

    # Sources: every feed stub at full share, plus a fraction of the short arm-to-arm
    # turn stubs so some cars enter already turning.
    sources = entry + ([] if a.no_platoons else turnstubs)
    per_src = (target / len(entry)) * (len(entry) / len(sources))
    gap = 3600.0 / per_src if per_src > 0 else 1.0
    got, n = [], 1
    for src in sources:
        t = rng.uniform(0, gap)
        while t < end:
            size = 1 if a.no_platoons else max(1, int(rng.expovariate(1 / (1.4 * burst_mult))) + 1)
            for _ in range(size):
                route = walk(edges, rng, src, rng.randint(lo_h, hi_h), hubs, a.avoid_hubs)
                if len(route) < 2:
                    continue
                got.append({
                    "t": round(t, 2),
                    "id": veh_id(n),
                    "route": route,
                    **depart_attrs(rng, edge_len(edges[src])),
                    "vtype": _pick(rng, VTYPE_W, VTYPES),
                })
                n += 1
                # Tight intra-platoon gap (0.6-1.4 s) so the tail actually queues behind.
                t += rng.uniform(0.6, 1.4)
                if t >= end:
                    break
            t += rng.uniform(0.3, gap * 1.5)

    # Southbound Taft platoons: long, dense, on a fixed cadence, few but heavy. These are
    # drafted from the same draws, so they scale with the seed like everything else.
    for k, src in enumerate(south):
        t = rng.uniform(0, 40) + 20 * k
        while t < end:
            size = max(4, int(rng.gauss(8, 2) * burst_mult))
            for i in range(size):
                route = walk(edges, rng, src, max(5, rng.randint(lo_h, hi_h + 3)), hubs, a.avoid_hubs)
                got.append({
                    "t": round(t, 2), "id": veh_id(n), "route": route,
                    "departLane": "best" if i % 3 else "free",
                    "departSpeed": "max" if edge_len(edges[src]) >= LONG_EDGE else "0",
                    "departPos": "free" if i or edge_len(edges[src]) < LONG_EDGE else "base",
                    "vtype": "car",
                })
                n += 1
                t += rng.uniform(0.5, 1.0)
            t += rng.uniform(50, 95)

    got.sort(key=lambda v: (v["t"], v["id"]))
    got = got[: a.max]

    hdr = {"scenario": a.scenario, "seed": a.seed, "scale": scale,
           "vehicles_per_h": round(target), "end_s": end, "vehicles": len(got),
           "route_steps": a.route_steps, "net": os.path.basename(a.net)}
    with open(a.out, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(f"<!-- {json.dumps(hdr)} -->\n")
        f.write('<routes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n')
        for vid, ln, acc, dec, vmax, gap, sig in VTYPES:
            f.write(f'  <vType id="{vid}" length="{ln}" accel="{acc}" decel="{dec}"'
                    f' maxSpeed="{vmax}" minGap="{gap}" sigma="{sig}" insertionChecks="false"'
                    f' carFollowModel="Krauss"/>\n')
        f.write(f"  <!-- {len(got)} vehicles; {len(entry)} entry approaches;"
                        f" {len(south)} Taft platoons; bursts x{burst_mult:g} -->\n")
        for v in got:
            f.write(f'  <vehicle id="{v["id"]}" type="{v["vtype"]}" depart="{v["t"]}"'
                    f' departLane="{v["departLane"]}" departSpeed="{v["departSpeed"]}"'
                    f' departPos="{v["departPos"]}">'
                    f'<route edges="{" ".join(v["route"])}"/></vehicle>\n')
        f.write("</routes>\n")
    print(f"{len(got)} vehicles over {end:g}s on {len(entry)} approaches -> {a.out}")


def _pick(rng, weights, table=None):
    if table is None:
        keys = list(weights)
        return rng.choices(keys, [weights[k] for k in keys])[0]
    return rng.choices(table, weights)[0][0]


if __name__ == "__main__":
    main()