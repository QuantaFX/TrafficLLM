"""Build the demo network.

Hand-built rather than taken from netgenerate, because netgenerate's short approach
stubs deadlock themselves: the stub is shorter than the inter-vehicle gap, so a
stopped leader sits across the junction and the follower cannot close up.

Layout: 3x3 signalised grid on a Taft Ave axis (A = north-south avenue, B = east-west
cross streets, C = outer avenue). Nodes are named <col><x><y>, x = east index, y = north
index, so col A sits on Taft Ave, col C on the outer avenue and row y=1 is the
east-west cross street. Every direction has 3 lanes -- lane 0 left turn only, lane 1
through, lane 2 through+right -- and every perimeter node has an entry/exit arm so
traffic flows in and out. Internal junctions stay plain traffic_light (signalised at
runtime). Connections, turns and lane assignment are produced by netconvert from a
node+edge file; retag() then enforces the lane semantics.
"""
import os
import subprocess
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
OUT = os.path.join(ASSETS, "taft.net.xml")
NODES_D = os.path.join(ASSETS, "_raw.nod.xml")
EDGES_D = os.path.join(ASSETS, "_raw.edg.xml")
SUMO_HOME = os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo")
NETCONVERT = os.path.join(SUMO_HOME, "bin", "netconvert.exe")
BLOCK, ARM, LANES, COLPITCH = 180, 240, 3, 780
COLS, ROWS, NCOLS = ["A", "B", "C"], 3, 3
STEPS = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}


def build():
    pos, inner, perim, used = {}, [], [], set()
    # Columns are separate streets running parallel: they must not sit on the same
    # coordinates, or every middle junction becomes a 15-approach pile-up of three
    # unrelated streets. A is the avenue, B the cross street at COLPITCH, C the outer
    # avenue one COLPITCH further out. Rows are BLOCK apart.
    for ci, c in enumerate(COLS):
        for x in range(NCOLS):
            for y in range(NCOLS):
                # Columns must clear the whole grid: adding COLPITCH per column collides
                # with the grid's own x axis (A10 at 180 vs B00 at 230 -> no link fits).
                pos[f"{c}{x}{y}"] = (ci * COLPITCH + x * BLOCK, y * BLOCK)
                used.add(pos[f"{c}{x}{y}"])
                (inner if 0 < x < NCOLS - 1 else perim).append(f"{c}{x}{y}")

    # arms off every perimeter node: one inbound (X_n), one outbound (n_X).
    # Compass direction comes from the geometry, not the column name: the columns
    # share x, so only one column can hang an arm off a given side.
    arm_pairs = set()
    grid = set(used)                      # snapshot: arms must not see each other
    for n in perim:
        x, y = pos[n]
        for d, (dx, dy) in STEPS.items():
            if any((px - x) * dx > 0 and (py - y) * dy >= 0 and (dx == 0 or py == y)
                   and (dy == 0 or px == x) for (px, py) in grid):
                continue  # another node lies on this ray: it is a grid link
            outer = f"{n}_{d}"
            pos[outer] = (x + ARM * dx, y + ARM * dy)
            arm_pairs.add((outer, n))
            arm_pairs.add((n, outer))

    edges = []
    # Grid links: exact neighbour steps. `<= COLPITCH` on a sum-of-abs distance drops
    # pairs sitting exactly on the boundary (float drift), which silently makes the grid
    # one-way. Arm links come from arm_pairs.
    linked = set()
    for a, b in ((a, b) for a in pos for b in pos if a < b):
        (ax, ay), (bx, by) = pos[a], pos[b]
        if (ax == bx and round(abs(ay - by)) == BLOCK) or (ay == by and round(abs(ax - bx)) == BLOCK):
            linked.update({(a, b), (b, a)})
    for a, b in sorted(linked | arm_pairs):
        edges.append((a, b))

    # Signalise every node that joins *two* grid lines (a same-column AND a same-row
    # neighbour). Perimeter nodes on the middle row/column still carry crossing traffic;
    # leaving them priority lets cross-conflict at an arm junction seal the grid.
    grid_neigh = {n: 0 for n in pos}
    for a, b in ((a, b) for a in pos for b in pos if a < b):
        (ax, ay), (bx, by) = pos[a], pos[b]
        if ax == bx and round(abs(ay - by)) == BLOCK or ay == by and round(abs(ax - bx)) == BLOCK:
            grid_neigh[a] += 1
            grid_neigh[b] += 1
    kind_of = {n: "traffic_light" if grid_neigh[n] >= 2 else "priority" for n in pos}

    nodes = ['<?xml version="1.0" encoding="UTF-8"?>', "<nodes>"]
    for n, (x, y) in pos.items():
        kind = kind_of[n]
        nodes.append(f'  <node id="{n}" x="{x}" y="{y}" type="{kind}"/>')
    nodes.append("</nodes>")
    elist = ['<?xml version="1.0" encoding="UTF-8"?>', "<edges>"]
    for a, b in edges:
        elist.append(f'  <edge id="e{a}_{b}" from="{a}" to="{b}" priority="10"'
                     f' numLanes="{LANES}" speed="16.67"/>')
    elist.append("</edges>")
    write(NODES_D, nodes)
    write(EDGES_D, elist)

    # Junction type traffic_light: netconvert emits the full legal turn set and picks
    # lanes. --tls.guess would prune turns, and --tls.discard-loaded drops the tlLogic
    # links its own link indexes refer to, so retag() renumbers every link itself.
    r = subprocess.run([NETCONVERT, "-n", NODES_D, "-e", EDGES_D, "-o", OUT,
                        "-j", "traffic_light", "--tls.discard-loaded"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(r.stderr or r.stdout)
    retag()
    print(f"{len(edges)} edges, {len(pos)} junctions -> {OUT}")


def write(path, lines):
    """Write a whole file and close it."""
    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def retag():
    """Reduce the netconvert output to a clean <net> and enforce lane semantics.

    netconvert appends its run-configuration blocks to the root element; they are
    dropped and the net kept. Every connection is re-laned: 0 = left + U-turn,
    1 = through, 2 = through + right (through is duplicated onto lane 2 so it stays
    a through lane). A from-lane holding more than one connection would be held, so
    duplicates are resolved directionally -- heaviest through movement dropped first,
    then heaviest Other -- and all link indexes are renumbered.
    """
    root = ET.parse(OUT).getroot()
    for e in list(root):
        if e.tag not in ("edge", "junction", "connection"):
            root.remove(e)

    conns, parts = [], []
    for c in list(root):
        if c.tag != "connection":
            continue
        root.remove(c)
        (parts if c.get("dir") == "part" else conns).append(c)

    groups = {}
    for c in conns:
        groups.setdefault((c.get("from"), c.get("fromLane")), []).append(c)
    weight = {"s": 0, "l": 1, "t": 2, "r": 3, "R": 4, "L": 5}
    keep = [min(g, key=lambda c: (weight.get(c.get("dir"), 9), c.get("to")))
            for g in groups.values()]

    for i, c in enumerate(keep):
        tag = c.get("dir")
        if tag in ("s", "t"):
            c.set("fromLane", "1")
            dup = ET.fromstring(ET.tostring(c, encoding="unicode"))
            dup.set("fromLane", "2")
            dup.set("linkIndex", str(2 * i + 1))
            keep.append(dup)
        elif tag in ("l", "L"):
            c.set("fromLane", "0")
        else:
            c.set("fromLane", "2")
        c.set("toLane", str(min(int(c.get("toLane")), LANES - 1)))
        c.set("linkIndex", str(2 * i))

    for c in parts + keep:
        root.append(c)
    ET.indent(root, "    ")
    write(OUT, ['<?xml version="1.0" encoding="UTF-8"?>',
                ET.tostring(root, encoding="unicode")])


if __name__ == "__main__":
    build()