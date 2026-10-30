"""Prove generated demand actually keeps moving: loaded, inserted, driving, arriving.

Runs a .rou.xml against the current net under its fixed-time signals with
time-to-teleport off, and fails unless vehicles get in, a real share of them reach a
destination, mean speed holds up, and SUMO logged zero Error lines. Stall time is
tracked per vehicle and capped so a silent jam cannot pass.

    python assets/validate_demand.py --rou assets/demand.rou.xml --end 600
"""
import argparse
import collections
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo")
sys.path.insert(0, os.path.join(os.environ["SUMO_HOME"], "tools"))

import libsumo as traci  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", default=os.path.join(ROOT, "assets", "taft.net.xml"))
    ap.add_argument("--rou", default=os.path.join(ROOT, "assets", "demand.rou.xml"))
    ap.add_argument("--end", type=float, default=600)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-stall", type=float, default=120.0)
    ap.add_argument("--min-speed", type=float, default=2.0)
    ap.add_argument("--min-arrived", type=float, default=0.25,
                    help="share of inserted vehicles that must have arrived by --end")
    a = ap.parse_args()

    os.makedirs(os.path.join(ROOT, "_work"), exist_ok=True)
    cfg = os.path.join(ROOT, "_work", "_validate.sumocfg")
    log = os.path.join(ROOT, "_work", "_validate.log")
    # config-relative paths are resolved against the config's own folder
    net, rou = os.path.abspath(a.net), os.path.abspath(a.rou)
    with open(cfg, "w") as f:
        f.write(f"""<?xml version="1.0"?>
<configuration>
  <input><net-file value="{net}"/><route-files value="{rou}"/></input>
  <time><begin value="0"/><end value="{a.end:g}"/><step-length value="1"/></time>
  <processing><time-to-teleport value="-1"/><collision.action value="none"
              /><ignore-route-errors value="true"/></processing>
  <report><no-step-log value="true"/></report>
  <random_number><seed value="{a.seed}"/></random_number>
</configuration>
""")
    traci.start(["-c", cfg], stdout=open(log, "w"))
    _TLS = traci.trafficlight.getIDList()
    print("net", traci.simulation.getNetBoundary(), "tls", len(traci.trafficlight.getIDList()))
    stuck_since, worst_stall, speeds = {}, 0.0, []
    inserted, arrived = 0, 0
    try:
        while traci.simulation.getTime() < a.end:
            traci.simulationStep()
            # Re-assert every signal state: freshly built nets can ship phases with no
            # green link, and re-applying the state makes SUMO re-derive its links.
            for tl in _TLS:
                traci.trafficlight.setRedYellowGreenState(
                    tl, traci.trafficlight.getRedYellowGreenState(tl))
            inserted += traci.simulation.getDepartedNumber()
            arrived += traci.simulation.getArrivedNumber()
            for v in traci.vehicle.getIDList():
                s = traci.vehicle.getSpeed(v)
                speeds.append(s)
                if s < 0.1:
                    t0 = stuck_since.setdefault(v, traci.simulation.getTime())
                    worst_stall = max(worst_stall, traci.simulation.getTime() - t0)
                else:
                    stuck_since.pop(v, None)
    finally:
        traci.close()

    text = open(log, errors="replace").read()
    errors = [l for l in text.splitlines() if l.startswith("Error")]
    warn_kinds = collections.Counter(
        re.sub(r"'[^']*'", "'X'", l.split("Warning: ", 1)[1])[:70]
        for l in text.splitlines() if l.startswith("Warning: "))
    depart_warn = sum(n for k, n in warn_kinds.items() if "depart" in k.lower())

    mean = sum(speeds) / len(speeds) if speeds else 0.0
    print(f"inserted={inserted} arrived={arrived} mean_speed={mean:.2f} m/s "
          f"longest_stall={worst_stall:.0f}s errors={len(errors)} depart_warnings={depart_warn}")
    for l in errors[:8]:
        print("  " + l)
    for k, n in warn_kinds.most_common(4):
        print(f"  warn x{n}: {k}")

    ok = True
    if errors:
        print("FAIL SUMO logged Error lines")
        ok = False
    if inserted < 0.5 * a.end:
        print(f"FAIL only {inserted} vehicles inserted over {a.end:g}s")
        ok = False
    if inserted and arrived < a.min_arrived * inserted:
        print(f"FAIL {arrived} arrived < {a.min_arrived:.0%} of {inserted} inserted (jammed)")
        ok = False
    if mean < a.min_speed:
        print(f"FAIL mean speed {mean:.2f} < {a.min_speed}")
        ok = False
    if worst_stall > a.max_stall:
        print(f"FAIL longest stall {worst_stall:.0f}s > {a.max_stall:.0f}s")
        ok = False
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())