"""Scenario classes B: healthy traffic vs induced gridlock, both backends.

gridlock net = sp9 (9 intersections, 4 with TLS) with 4x demand and
--time-to-teleport -1, so queues spill back through junctions and deadlock
persists instead of being auto-cleared by teleports.
Healthy  net = sp9 with the default demand.

For each (net, mode): step to --end at 1 s, then report
- steps/sec, ns/step (best of --reps)
- final-state gridlock indicators: mean timeLoss, number of vehicle
  insertions deferred (waiting), and whether the sim reached --end.
"""
import argparse, json, os, statistics, sys, time

os.environ.setdefault("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo")
os.environ["PATH"] = os.path.join(os.environ["SUMO_HOME"], "bin") + os.pathsep + os.environ["PATH"]

p = argparse.ArgumentParser()
p.add_argument("--mode", choices=["traci", "libsumo"], required=True)
p.add_argument("--net", required=True)
p.add_argument("--routes", required=True)
p.add_argument("--end", type=int, default=1200)
p.add_argument("--reps", type=int, default=3)
p.add_argument("--port", type=int, default=8879)
p.add_argument("--label", default="")
a = p.parse_args()

if a.mode == "libsumo":
    import libsumo as C
else:
    import traci as C

ARGS = ["sumo", "-n", os.path.abspath(a.net), "-r", os.path.abspath(a.routes),
        "--no-warnings", "--no-step-log", "--seed", "42",
        "--time-to-teleport", "-1", "--max-depart-delay", "-1"]


def run_once():
    if a.mode == "libsumo":
        C.start(ARGS)
    else:
        C.start(ARGS, port=a.port)
    n = 0
    t0 = time.perf_counter()
    while True:
        C.simulationStep()
        n += 1
        if C.simulation.getTime() >= a.end or not C.simulation.getMinExpectedNumber():
            break
    dt = time.perf_counter() - t0
    sim_t = C.simulation.getTime()
    tl = C.simulation.getTimeLossList() if hasattr(C.simulation, "getTimeLossList") else []
    tl_ids = C.vehicle.getIDList()
    losses = [C.vehicle.getTimeLoss(v) for v in tl_ids]
    waiting = C.simulation.getEndingTeleportNumber(), len(C.simulation.getDepartedIDList())
    out = {
        "wall_s": dt, "steps": n, "sim_end_reached": sim_t,
        "running": C.simulation.getMinExpectedNumber(),
        "mean_time_loss": round(statistics.mean(losses), 1) if losses else None,
        "max_time_loss": round(max(losses), 1) if losses else None,
        "teleports": waiting[0],
    }
    C.close()
    return out


runs = [run_once() for _ in range(a.reps)]
best = min(runs, key=lambda r: r["steps"] / r["wall_s"] and r["wall_s"])
res = {
    "label": a.label, "mode": a.mode,
    "steps": best["steps"], "sim_end_reached": best["sim_end_reached"],
    "vehicles_still_running": best["running"],
    "mean_time_loss_s": best["mean_time_loss"], "max_time_loss_s": best["max_time_loss"],
    "steps_per_sec_best": round(best["steps"] / best["wall_s"], 1),
    "ns_per_step_best": round(best["wall_s"] / best["steps"] * 1e9, 1),
}
print(json.dumps(res))
with open("scen.jsonl", "a") as f:
    f.write(json.dumps(res) + "\n")