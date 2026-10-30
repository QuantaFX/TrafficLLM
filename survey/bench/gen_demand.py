"""Demand generator for signal plans that model *blocked boxes*.

sumo-rl's shipped 3x3grid net has a problem worth measuring: every seed turns
right, nothing turns left into the opposite arm, so per-edge lastStepVehicleNumber
never exceeds ~4 cars. A gridlock benchmark needs demand that fills buffers and
spills back.

Modes
  fixed     explicit flows (as in the hand-written spur demand)
  shuffled  every edge draws equally weighted destinations
  oncoming  demand concentrated on the axis whose opposite arm is short

Writes a .rou.xml with <vType> + <flow> (tuples start, end, vph when shuffled,
otherwise fixed per-edge rates taken from --rate).

Usage:
  python gen_demand.py --net NET --mode shuffled --rate 1200 --end 3600 --seed 7 -o out.rou.xml
"""
import argparse, random, sys

import sumolib

p = argparse.ArgumentParser()
p.add_argument("--net", required=True)
p.add_argument("--mode", choices=["fixed", "shuffled", "oncoming"], default="shuffled")
p.add_argument("--rate", type=float, default=1200.0)
p.add_argument("--end", type=int, default=3600)
p.add_argument("--seed", type=int, default=7)
p.add_argument("-o", required=True)
a = p.parse_args()

random.seed(a.seed)
net = sumolib.net.readNet(a.net, withInternal=False)

edges = [e for e in net.getEdges() if e.getFunction() not in ("internal", "walkingarea")]
by_from = {}
for e in edges:
    by_from.setdefault(e.getFromNode().getID(), []).append(e)
routes = []  # (first_edge, last_edge)


def connected(first_id, last_id):
    """shipped nets are partial; keep only pairs joined by a directed path."""
    try:
        return net.getEdge(first_id).getToNode().getID() in \
            {n.getID() for n in net.getReachable(first_id and net.getEdge(first_id), maxLength=5000)}
    except Exception:
        return True


def destinations(e, maxlen):
    """Edges reachable from e's head node in 1..maxlen hops."""
    out = []
    node = e.getToNode()
    if node.getID().startswith(":"):
        return out
    frontier = [node]
    for _ in range(maxlen):
        nxt = []
        for n in frontier:
            for o in by_from.get(n.getID(), []):
                if o is e:
                    continue
                out.append(o)
                nxt.append(o.getToNode())
        frontier = nxt
    return out


for e in edges:
    node = e.getToNode()
    if node.getID().startswith(":"):
        continue
    dests = destinations(e, 3 if a.mode != "oncoming" else 1)
    if not dests:
        continue
    if a.mode == "shuffled":
        random.shuffle(dests)
        dests = dests[: max(1, len(dests) // 3)]
    elif a.mode == "oncoming":
        ang = e.getToNode().getCoord()
        dests = sorted(dests, key=lambda o: abs(o.getToNode().getCoord()[0] - ang[0])
                       + abs(o.getToNode().getCoord()[1] - ang[1]))
    for d in dests:
        if connected(e.getID(), d.getID()):
            routes.append((e.getID(), d.getID()))

# de-dup, cap fan-out
seen, uniq = set(), []
for r in routes:
    if r in seen:
        continue
    seen.add(r)
    uniq.append(r)
routes = uniq

per_route = a.rate / max(len(routes) / 2.0, 1.0)
with open(a.o, "w") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n<routes>\n')
    f.write('  <vType id="car" length="5" minGap="2" accel="2.6" decel="4.5" maxSpeed="13.89"/>\n')
    for i, (first, last) in enumerate(routes):
        f.write(f'  <flow id="f{i}" type="car" begin="0" end="{a.end}" '
                f'vehsPerHour="{per_route:.1f}" departLane="free" departSpeed="max">\n')
        f.write(f'    <route edges="{first} {last}"/>\n  </flow>\n')
    f.write("</routes>\n")
print(f"{len(routes)} flows, {per_route:.0f} veh/h each, total {per_route * len(routes):.0f} veh/h")