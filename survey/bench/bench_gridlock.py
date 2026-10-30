"""Gridlock-relevant metrics for an LLM-in-the-loop TSC design.

Runs a scenario with --time-to-teleport -1 (so deadlock persists) and records,
on the SAME run: backend throughput AND the state an orchestrator would have to
detect gridlock from — per-lane queue occupancy, per-intersection blocked-box
count, and how much it costs to compute. Writes JSON to gridlock_metrics.json.
"""
import argparse, json, os, statistics, sys, time

os.environ.setdefault("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo")
os.environ["PATH"] = os.path.join(os.environ["SUMO_HOME"], "bin") + os.pathsep + os.environ["PATH"]

ap = argparse.ArgumentParser()
ap.add_argument("--mode", choices=["traci", "libsumo"], required=True)
ap.add_argument("--net", required=True)
ap.add_argument("--routes", required=True)
ap.add_argument("--end", type=int, default=1800)
ap.add_argument("--port", type=int, default=8897)
ap.add_argument("--label", default="")
a = ap.parse_args()

if a.mode == "libsumo":
    import libsumo as C
else:
    import traci as C

ARGS = ["sumo", "-n", os.path.abspath(a.net), "-r", os.path.abspath(a.routes),
        "--no-warnings", "--no-step-log", "--seed", "42",
        "--time-to-teleport", "-1", "--max-depart-delay", "-1"]

if a.mode == "libsumo":
    C.start(ARGS)
else:
    C.start(ARGS, port=a.port)

tl = list(C.trafficlight.getIDList())
tl_lanes = {t: sorted(set(C.trafficlight.getControlledLanes(t))) for t in tl}
edges_of = {t: sorted({C.lane.getEdgeID(l) for l in ls}) for t, ls in tl_lanes.items()}

n = 0
t_start = time.perf_counter()
samples = []  # (sim_time, blocked_boxes, mean_occupancy, n_stopped_vehicles)
crowd_cost = []
while C.simulation.getTime() < a.end and C.simulation.getMinExpectedNumber():
    C.simulationStep()
    n += 1
    if n % 10 == 0:  # orchestrator cadence: every 10 sim-seconds
        t0 = time.perf_counter()
        blocked = 0
        halted = 0
        occ = []
        for t, ls in tl_lanes.items():
            for l in ls:
                v = C.lane.getLastStepHaltingNumber(l)
                occ.append(C.lane.getLastStepOccupancy(l))
                halted += v
                if v >= 3:  # >=3 stopped vehicles on one lane == that lane's box is blocked
                    blocked += 1
                # a lane whose downstream junction cannot discharge == blocked box
        for e in {x for ls in edges_of.values() for x in ls}:
            if C.edge.getLastStepHaltingNumber(e) >= 1 and C.edge.getLastStepOccupancy(e) > 60:
                blocked += 1
        crowd = len(C.simulation.getDepartedIDList())
        crowd_cost.append(time.perf_counter() - t0)
        samples.append((C.simulation.getTime(), blocked, statistics.mean(occ) if occ else 0.0,
                        sum(C.lane.getLastStepHaltingNumber(l) for ls in tl_lanes.values() for l in ls)))
wall = time.perf_counter() - t_start
loss = [C.vehicle.getTimeLoss(v) for v in C.vehicle.getIDList()]
res = {
    "label": a.label, "mode": a.mode, "steps": n, "sim_s": C.simulation.getTime(),
    "truncated_by_end": C.simulation.getMinExpectedNumber() > 0,
    "steps_per_sec": round(n / wall, 1), "ns_per_step": round(wall / n * 1e9, 1),
    "vehicles_running": C.simulation.getMinExpectedNumber(),
    "mean_time_loss_s": round(statistics.mean(loss), 1) if loss else None,
    "max_time_loss_s": round(max(loss), 1) if loss else None,
    "blocked_edges_last": samples[-1][1] if samples else None,
    "blocked_edges_max": max(s[1] for s in samples) if samples else None,
    "mean_queue_last": samples[-1][3] if samples else None,
    "orchestrator_probe_us": round(statistics.median(crowd_cost) * 1e6, 1) if crowd_cost else None,
    "queue_last_over_median": (round(samples[-1][3] / max(statistics.median(s[3] for s in samples), 1), 2)
                               if samples else None),
    "blocked_window_first_half": sum(s[1] for s in samples[: len(samples) // 2]),
    "blocked_window_second_half": sum(s[1] for s in samples[len(samples) // 2:]),
    "blocked_series": [s[1] for s in samples],
}
C.close()
print(json.dumps({k: v for k, v in res.items() if k != "blocked_series"}))
with open("gridlock_metrics.json", "a") as f:
    f.write(json.dumps(res) + "\n")