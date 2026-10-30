"""Throughput + observation-extraction benchmark for SUMO (traci vs libsumo).

Single-threaded. Measures:
  A) simulation-only stepping cost (step + nothing else)
  B) per-step cost of the observation an LLM agent actually needs:
     per-lane queued/halted vehicle counts for every controlled intersection
  C) full zero-delay control-loop cost: observe -> decide (mock) -> apply phase

Usage:
  python bench_step.py --mode traci|libsumo --net X --routes Y --end N
                       [--observe] [--control] [--reps 3] [--warmup 30]
Prints one JSON line per mode with steps/sec and ns/step.
"""
import argparse, json, os, statistics, sys, time

SUMO_BIN = r"C:\Program Files (x86)\Eclipse\Sumo\bin"
os.environ.setdefault("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo")
SUMO_BIN_ABS = os.path.join(os.environ["SUMO_HOME"], "bin", "sumo.exe")
PATH = os.environ.get("PATH", "")
if os.path.join(os.environ["SUMO_HOME"], "bin") not in PATH:
    os.environ["PATH"] = os.path.join(os.environ["SUMO_HOME"], "bin") + os.pathsep + PATH

p = argparse.ArgumentParser()
p.add_argument("--mode", required=True, choices=["traci", "libsumo"])
p.add_argument("--net", required=True)
p.add_argument("--routes", required=True)
p.add_argument("--end", type=int, default=1200)
p.add_argument("--reps", type=int, default=3)
p.add_argument("--warmup", type=int, default=60)
p.add_argument("--observe", action="store_true")
p.add_argument("--control", action="store_true")
p.add_argument("--port", type=int, default=8873)
p.add_argument("--ctl-every", type=int, default=1)
p.add_argument("--baseline-ns", type=float, default=0.0)
p.add_argument("--detailed", action="store_true")
a = p.parse_args()

if a.mode == "libsumo":
    import libsumo as C
else:
    import traci as C

ARGS = ["-n", os.path.abspath(a.net), "-r", os.path.abspath(a.routes),
        "--no-warnings", "--no-step-log", "--seed", "42",
        "--time-to-teleport", "-1", "--no-internal-links", "false"]


def start():
    if a.mode == "libsumo":
        C.start(["sumo", *ARGS])
    else:
        C.start(["sumo", *ARGS], port=a.port)


def stop():
    C.close()


def tls_lanes():
    out = {}
    for t in C.trafficlight.getIDList():
        lanes = sorted(set(C.trafficlight.getControlledLanes(t)))
        out[t] = lanes
    return out


# how many calls we *intend* per step (used to report call counts without I/O)
_pending = {"tls": [], "logic": {}}


def observe(tl_lanes):
    """The LLM agent's minimal observation: halted count + vehicle count per lane."""
    obs = {}
    for t, lanes in tl_lanes.items():
        obs[t] = [(C.lane.getLastStepHaltingNumber(l), C.lane.getLastStepVehicleNumber(l))
                  for l in lanes]
    return obs


def decide_apply(tl_lanes):
    """Zero-delay mock agent: cycle phase index each second (setPhase X2 per tick)."""
    for t in tl_lanes:
        try:
            nph = len(C.trafficlight.getAllProgramLogics(t)[0].phases)
        except Exception:
            continue
        C.trafficlight.setPhase(t, int(C.simulation.getTime()) % nph)


def run_once():
    start()
    C.simulationStep()
    tl = tls_lanes()
    n_tls = len(tl)
    tls_last.clear()
    tls_last.extend(tl)
    for _ in range(a.warmup):
        C.simulationStep()
    n = n_obs = n_ctl = 0
    ctl_every = a.ctl_every
    t0 = time.perf_counter()
    while True:
        if C.simulation.getTime() >= a.end or not C.simulation.getMinExpectedNumber():
            break
        C.simulationStep()
        n += 1
        if a.observe:
            observe(tl)
            n_obs += 1
        if a.control and (ctl_every == 1 or n % ctl_every == 0):
            decide_apply(tl)
            n_ctl += 1
    dt = time.perf_counter() - t0
    sim_t = C.simulation.getTime()
    running = C.simulation.getMinExpectedNumber()
    stop()
    return dt, n, sim_t, n_obs, n_ctl, n_tls, running


RESULT_FILE = "results.jsonl"
tls_last = []


baseline_ns = {}
for _r in range(a.reps):
    baseline_ns[_r] = None
# calibration: an uninterrupted plain-stepping pass right before the measured pass
calib = []
for _r in range(a.reps):
    start()
    C.simulationStep()
    for _ in range(a.warmup):
        C.simulationStep()
    t0 = time.perf_counter()
    k = 0
    while k < 120 and C.simulation.getTime() < a.end:
        C.simulationStep()
        k += 1
    calib.append((time.perf_counter() - t0) / k * 1e9)
    stop()
BASE_NS = min(calib)
runs = []
for _ in range(a.reps):
    runs.append(run_once())
best = min(runs, key=lambda r: r[0])
dts = [r[0] for r in runs]
n = best[1]
med = statistics.median(dts)
out = {
    "mode": a.mode, "observe": a.observe, "control_every_s": a.ctl_every if a.control else 0,
    "steps": n,
    "vehicles_still_running": best[6],
    "sim_seconds_advanced": round(best[2], 1),
    "obs_calls_per_step": 2 * len(tls_last),
    "ctl_ticks": best[4],
    "wall_s_median": round(med, 3),
    "wall_s_best": round(best[0], 3),
    "steps_per_sec": round(n / best[0], 1),
    "ns_per_step": round(best[0] / n * 1e9, 1),
    "ns_per_step_median": round(med / n * 1e9, 1),
}
if a.observe:
    base = a.baseline_ns or BASE_NS
    out["obs_overhead_ns_per_step"] = round(best[0] / n * 1e9 - base, 1)
if a.control:
    # control ticks are batched per N steps; amortise over sim steps
    out["ns_per_step_incl_control"] = round(best[0] / n * 1e9, 1)
# --- warmed-up "observe only" extras: sub-window timing + all-lane variant ---
def run_detailed():
    """Warm up fully, then time (a) a 120-step observe-only window and
    (b) a 120-step window observing EVERY lane with 4 lane calls."""
    start()
    C.simulationStep()
    for _ in range(max(a.warmup, 120)):
        C.simulationStep()
    tl = tls_lanes()
    t_obs, n_o = 0.0, 0
    t0 = time.perf_counter()
    while n_o < 120 and C.simulation.getTime() < a.end:
        C.simulationStep(); n_o += 1
        t = time.perf_counter()
        observe(tl)
        t_obs += time.perf_counter() - t
    all_lanes = C.lane.getIDList()
    t_all, n_a = 0.0, 0
    t0 = time.perf_counter()
    while n_a < 120 and C.simulation.getTime() < a.end:
        C.simulationStep(); n_a += 1
        t = time.perf_counter()
        for l in all_lanes:
            C.lane.getLastStepHaltingNumber(l)
            C.lane.getLastStepVehicleNumber(l)
            C.lane.getLastStepMeanSpeed(l)
            C.lane.getLastStepOccupancy(l)
        t_all += time.perf_counter() - t
    dt = time.perf_counter() - t0
    stop()
    return {
        "calls_per_step_tls": 2 * len(tl), "n_lanes": len(all_lanes),
        "observe_only_ns_per_step": round(t_obs / max(n_o, 1) * 1e9, 1),
        "observe_all_lanes_4calls_ns_per_step": round(t_all / max(n_a, 1) * 1e9, 1),
    }


extra = run_detailed() if a.detailed else {}
if extra:
    out.update(extra)
print(json.dumps(out))
with open(RESULT_FILE, "a") as f:
    f.write(json.dumps(out) + "\n")