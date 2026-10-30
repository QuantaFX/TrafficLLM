"""Isolated per-call latency, warmed up, 2000 reps each, on the 4-TLS spur net.

Measures one call of each observation primitive an LLM agent would need, so the
cost of an observation bundle can be computed from first principles instead of a
single rolled-up wall clock. Raw socket (traci) and in-process (libsumo) compared.
"""
import json, os, statistics, time

LANE_COUNT = 2000

def bench(C, label):
    tls = list(C.trafficlight.getIDList())
    tl0 = tls[0]
    lanes = sorted(set(C.trafficlight.getControlledLanes(tl0)))
    all_lanes = list(C.lane.getIDList())[:400]
    probes = {
        "simulation.getTime": lambda: C.simulation.getTime(),
        "simulation.getMinExpectedNumber": lambda: C.simulation.getMinExpectedNumber(),
        "trafficlight.getIDList": lambda: C.trafficlight.getIDList(),
        "trafficlight.getControlledLanes(tl)": lambda: C.trafficlight.getControlledLanes(tl0),
        "trafficlight.getRedYellowGreenState(tl)": lambda: C.trafficlight.getRedYellowGreenState(tl0),
        "lane.getLastStepHaltingNumber(lane)": lambda: C.lane.getLastStepHaltingNumber(lanes[0]),
        "trafficlight.setPhase(tl, i)": lambda: C.trafficlight.setPhase(tl0, 0),
        "lane.getIDList": lambda: C.lane.getIDList(),
    }
    res = {}
    for name, fn in probes.items():
        fn()
        ts = []
        for _ in range(LANE_COUNT):
            t = time.perf_counter(); fn(); ts.append(time.perf_counter() - t)
        res[name] = round(statistics.median(ts) * 1e6, 2)  # microseconds
    # one full "observe everything" bundle for the whole net
    ts = []
    for _ in range(200):
        t = time.perf_counter()
        for tl in tls:
            for l in C.trafficlight.getControlledLanes(tl):
                C.lane.getLastStepHaltingNumber(l)
                C.lane.getLastStepVehicleNumber(l)
        ts.append(time.perf_counter() - t)
    res["FULL_observe_bundle_us"] = round(statistics.median(ts) * 1e6, 1)
    res["_lanes_in_bundle"] = 2 * sum(len(C.trafficlight.getControlledLanes(t)) for t in tls)
    # single batched control tick for all TLS
    ts = []
    for i in range(500):
        t = time.perf_counter()
        for tl in tls:
            C.trafficlight.setPhase(tl, i % 2)
        ts.append(time.perf_counter() - t)
    res["FULL_control_tick_us"] = round(statistics.median(ts) * 1e6, 1)
    print(json.dumps({label: res}, indent=1))


NET, ROU = "broken/sp9.net.xml", "broken/spur.rou.xml"
BASE = ["sumo", "-n", NET, "-r", ROU, "--no-warnings", "--no-step-log", "--seed", "42",
        "--time-to-teleport", "-1"]

import libsumo as L
L.start(BASE)
for _ in range(300):
    L.simulationStep()
bench(L, "libsumo_us")
L.close()

import traci as T
T.start(BASE, port=8891)
for _ in range(300):
    T.simulationStep()
bench(T, "traci_us")
T.close()