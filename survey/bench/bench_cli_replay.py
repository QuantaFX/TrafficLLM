"""CLI-replay design cost: each agent tick is one `sumo` invocation seeded and
`--end`-bounded, replaying demand; the agent reads each run's --queue-output XML.
Measures wrapper wall time for a 60 s replay on the 9-intersection net."""
import json, os, subprocess, sys, time

SUMO = os.path.join(os.environ["SUMO_HOME"], "bin", "sumo.exe")
NET, ROU, ROUTE_T = sys.argv[1], sys.argv[2], os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "broken", "spur_route0.rou.xml")

# materialise a static route file (equivalent to the flow definitions)
subprocess.run([os.path.join(os.environ["SUMO_HOME"], "bin", "duarouter.exe"),
                "-n", NET, "-r", ROU, "-o", ROUTE_T, "--no-step-log",
                "--seed", "42", "--repair", "true"], capture_output=True)

res = []
for _ in range(5):
    t0 = time.perf_counter()
    r = subprocess.run([SUMO, "-n", NET, "-r", ROU, "--no-warnings", "--no-step-log",
                        "--seed", "42", "--time-to-teleport", "-1",
                        "--begin", "0", "--end", "60", "--queue-output", "q.xml"],
                       capture_output=True, text=True)
    dt = time.perf_counter() - t0
    res.append(dt)
res.sort()
print(json.dumps({"process_spawn_60s_replay_ms": round(res[0] * 1000, 1),
                  "process_spawn_60s_replay_ms_median": round(res[2] * 1000, 1),
                  "exit": r.returncode, "stderr_tail": r.stderr[-200:]}))