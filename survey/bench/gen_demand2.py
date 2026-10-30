"""Demand generator v2: enumerate connected 2-edge routes once, then emit flows.

Avoids the per-try clean loop by only using edge pairs the net can actually route.
Modes: `cross` concentrates demand on the opposite arm (worst case for gridlock),
`spread` samples the full reachable set with a fixed seed.
"""
import argparse, random

import sumolib

p = argparse.ArgumentParser()
p.add_argument("--net", required=True)
p.add_argument("--mode", choices=["cross", "spread"], default="cross")
p.add_argument("--rate", type=float, default=1500.0, help="total veh/h")
p.add_argument("--seed", type=int, default=7)
p.add_argument("--end", type=int, default=3600)
p.add_argument("-o", required=True)
a = p.parse_args()

random.seed(a.seed)
net = sumolib.net.readNet(a.net, withInternal=False)
edges = [e for e in net.getEdges() if e.getFunction() not in ("internal", "walkingarea")]

# outgoing real edges per node
succ = {}
for e in edges:
    succ.setdefault(e.getFromNode().getID(), []).append(e)

pairs = []
for e in edges:
    head = e.getToNode()
    if head.getID().startswith(":"):
        continue
    for mid in succ.get(head.getID(), []):
        tail = mid.getToNode()
        if tail.getID().startswith(":"):
            continue
        if tail is e.getFromNode():
            continue  # U-turn back into the arm we came from
        if not e.allows("passenger") or not mid.allows("passenger"):
            continue
        pairs.append((e, mid))

if a.mode == "cross":
    def score(pm):
        e, mid = pm
        v1 = (e.getToNode().getCoord()[0] - e.getFromNode().getCoord()[0],
              e.getToNode().getCoord()[1] - e.getFromNode().getCoord()[1])
        v2 = (mid.getToNode().getCoord()[0] - mid.getFromNode().getCoord()[0],
              mid.getToNode().getCoord()[1] - mid.getFromNode().getCoord()[1])
        return abs(v1[0] * v2[1] - v1[1] * v2[0])  # 0 = straight on, large = turn
    pairs.sort(key=score, reverse=True)  # left/right turns first
    pairs = pairs[: max(8, len(pairs) // 3)]
else:
    random.shuffle(pairs)
    pairs = pairs[: max(8, len(pairs) // 2)]

per = a.rate / max(len(pairs), 1)
with open(a.o, "w") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n<routes>\n')
    f.write('  <vType id="car" length="5" minGap="2" accel="2.6" decel="4.5" maxSpeed="13.89"/>\n')
    for i, (e, mid) in enumerate(pairs):
        f.write(f'  <flow id="f{i}" type="car" begin="0" end="{a.end}" vehsPerHour="{per:.1f}" '
                f'departLane="free" departSpeed="max">\n    <route edges="{e.getID()} {mid.getID()}"/>\n  </flow>\n')
    f.write("</routes>\n")
print(f"{len(pairs)} turn routes, {per:.0f} veh/h each, total {per * len(pairs):.0f} veh/h")