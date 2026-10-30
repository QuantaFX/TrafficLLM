"""Floor case: plain `sumo` headless, no Python at all, with SUMO's own
--queue-output XML export. Establishes how fast SUMO alone can advance this net,
i.e. the upper bound any Python-in-the-loop design is measured against."""
import json, os, subprocess, sys, time

SUMO = os.path.join(os.environ["SUMO_HOME"], "bin", "sumo.exe")
name, net, routes, end, reps = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
out = f"q_{name}.xml"
best = 1e18
for _ in range(reps):
    t0 = time.perf_counter()
    r = subprocess.run([SUMO, "-n", net, "-r", routes, "--no-warnings", "--no-step-log",
                        "--seed", "42", "--time-to-teleport", "-1", "--end", end,
                        "--queue-output", out],
                       capture_output=True, text=True)
    dt = time.perf_counter() - t0
    best = min(best, dt)
    if r.returncode:
        print(r.stderr[-800:])
steps = float(end)
print(json.dumps({"case": name, "mode": "plain-sumo+queue-output",
                  "wall_s_best": round(best, 3), "steps_per_sec": round(steps / best, 1),
                  "ns_per_step": round(best / steps * 1e9, 1)}))