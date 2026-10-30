"""Export assets/taft.net.xml to ui/network.json for the map UI.

The UI needs geometry, not SUMO ids: lane polylines, junction polygons and a turn
arrow per connection. Signalised nodes carry g=1 so the UI can draw a signal bar.
"""
import json
import math
import os
import sys

import sumolib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = os.path.join(ROOT, "assets", "taft.net.xml")
OUT = os.path.join(ROOT, "ui", "network.json")

SIGNAL = {"traffic_light", "traffic_light_right_on_red"}


def seg(shape, lane_w, offset):
    """Offset a polyline sideways so the three lanes of an edge do not overlap."""
    pts = list(shape)
    if len(pts) < 2 or not offset:
        return pts
    (x0, y0), (x1, y1) = pts[0], pts[1]
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy) or 1.0
    ox, oy = -dy / n * offset, dx / n * offset
    return [(x + ox, y + oy) for x, y in pts]


def main():
    net = sumolib.net.readNet(NET)
    nodes, edges, turns = [], [], []

    for j in net.getNodes():
        kind = j.getType()
        nodes.append({"id": j.getID(), "x": j.getCoord()[0], "y": j.getCoord()[1],
                      "g": 1 if kind in SIGNAL else 0,
                      "p": [list(p) for p in (j.getShape() or [j.getCoord()])]})

    lane_w = 3.2
    for e in net.getEdges():
        lanes = []
        for lane in e.getLanes():
            off = (lane.getIndex() - (e.getLaneNumber() - 1) / 2) * lane_w
            lanes.append({"id": lane.getID(), "s": seg(lane.getShape(), lane_w, off)})
        edges.append({"id": e.getID(), "l": lanes})

    for n in net.getNodes():
        c = n.getCoord()
        for o in n.getOutgoing():
            for con in n.getConnections(o):
                shape = [c]
                lane = con.getFromLane()
                shape = list(lane.getShape()) + [con.getToLane().getShape()[-1]]
                shape = shape[-1:] if len(shape) < 2 else shape
                turns.append({"k": con.getDirection(), "s": [list(p) for p in shape]})

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"nodes": nodes, "edges": edges, "turns": turns}, f)
    print(f"{len(nodes)} nodes, {len(edges)} edges, {len(turns)} turns -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())