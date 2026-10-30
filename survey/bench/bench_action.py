"""Isolated action cost: how long does one batched control tick take
(getTime + getAllProgramLogics + setPhase per TLS) in traci vs libsumo.
1000 reps after a 200-step warm-up; reported as ns per tick and ns per
batch, independent of simulation stepping."""
import json, os, statistics, sys, time

import libsumo as L

N = 1000
out = {}
for mode in ("libsumo", "traci"):
    sys.argv = ["x"]
    if mode == "libsumo":
        C = L
        C.start(["sumo", "-n", "broken/sp9.net.xml", "-r", "broken/spur.rou.xml",
                 "--no-warnings", "--no-step-log", "--seed", "42", "--time-to-teleport", "-1"])
    else:
        from pathlib import Path
        import subprocess
        sys.path.insert(0, r"C:\Users\narch\Desktop\TrafficLLM\.surbey-venv\Lib\site-packages")
        sys.path.insert(0, os.environ["SUMO_HOME"] + r"\tools")
        import traci as C
        C.start(["sumo", "-n", "broken/sp9.net.xml", "-r", "broken/spur.rou.xml",
                 "--no-warnings", "--no-step-log", "--seed", "42", "--time-to-teleport", "-1"])
    for _ in range(200):
        C.simulationStep()
    tls = list(C.trafficlight.getIDList())
    nph = {t: len(C.trafficlight.getAllProgramLogics(t)[0].phases) for t in tls}
    samples = []
    for i in range(N):
        C.simulationStep()
        t = time.perf_counter()
        for tl in tls:
            C.trafficlight.setPhase(tl, int(C.simulation.getTime()) % nph[tl])
        samples.append(time.perf_counter() - t)
    C.close()
    out[mode] = {
        "tls": len(tls),
        "ctl_tick_ns_median": round(statistics.median(samples) * 1e9, 1),
        "ctl_tick_ns_mean": round(statistics.mean(samples) * 1e9, 1),
        "ctl_tick_ns_min": round(min(samples) * 1e9, 1),
        "per_tls_set_ns": round(statistics.median(samples) / len(tls) * 1e9, 1),
    }
print(json.dumps(out, indent=1))