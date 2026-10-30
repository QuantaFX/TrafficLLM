"""Generate turn-heavy demand for assets/taft.net.xml.

Random forward walks weighted toward turns, so the platoon/turn conditions the demo
needs actually occur. Seed-stable for a given --seed.
"""
import argparse, os, random, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = os.path.join(ROOT, "assets", "taft.net.xml")
SUMO = os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo")
sys.path.insert(0, os.path.join(SUMO, "tools"))

WEIGHT = {"l": 2.5, "r": 1.8, "L": 0.6, "R": 0.8, "t": 1.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--veh", type=int, default=240, help="veh/h per boundary approach")
    ap.add_argument("--end", type=int, default=3600)
    ap.add_argument("--len", type=int, default=5, help="max hops per vehicle")
    ap.add_argument("--out", default=os.path.join(ROOT, "assets", "demand.rou.xml"))
    a = ap.parse_args()
    import sumolib

    random.seed(a.seed)
    net = sumolib.net.readNet(NET)
    # Sources are the outbound edges of the arm nodes (X_Y), i.e. traffic entering the
    # grid from outside. A sink is the mirror: an arm node's inbound edge ends the trip.
    # One source per arm node: an arm has several outbound edges (one per target lane)
    # and counting them all multiplies the rate by three.
    seen = {}
    for n in net.getNodes():
        if "_" in n.getID():
            for e in n.getOutgoing():
                seen.setdefault(n.getID(), e)
    sources = list(seen.values())
    # --veh is vehicles per hour per source, so the mean gap is 3600/veh seconds.
    t_density = 3600.0 / a.veh
    got, rid = [], 0
    for src in sources:
        t = random.uniform(0, t_density)
        while t < a.end:
            route, cur = [src], src
            for _ in range(random.randint(3, a.len)):
                outs = [o for o in cur.getOutgoing().keys() if o not in route]
                if not outs:
                    break
                weights = []
                for o in outs:
                    conns = cur.getToNode().getConnections(cur, o)
                    d = conns[0].getDirection() if conns else "s"
                    weights.append(WEIGHT.get(d, 1.0))
                cur = random.choices(outs, weights)[0]
                route.append(cur)
            if len(route) >= 2:
                rid += 1
                # Short edges cannot hold a car at max speed: SUMO then warns "junction
                # too close" and refuses the departure. Free placements + speed 0 there.
                short = src.getLength() < 40
                pos = "random_free" if short else "free"
                spd = "0" if short else "max"
                got.append((t, f'  <vehicle id="v{rid:05d}" type="passenger" depart="{t:.2f}"'
                                f' departLane="best" departSpeed="{spd}" departPos="{pos}">'
                                f'<route edges="{" ".join(e.getID() for e in route)}"/></vehicle>'))
            t += random.expovariate(1 / t_density)

    with open(a.out, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<routes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
                ' xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/routes_file.xsd">\n')
        f.write('  <vType id="passenger" length="4.5" accel="2.6" decel="4.5" sigma="0.5"/>\n')
        # Must be sorted by depart: SUMO warns "should be sorted" and silently drops
        # every out-of-order vehicle. Sources are iterated one after another, so the
        # append order interleaves departure times.
        got.sort(key=lambda x: x[0])
        f.write("\n".join(v for _, v in got) + "\n</routes>\n")
    print(f"{rid} vehicles over {a.end}s -> {a.out}")


if __name__ == "__main__":
    main()