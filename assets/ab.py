import libsumo as traci, sys, subprocess, os
cfg = sys.argv[1:]
subprocess.run([r"C:\Program Files (x86)\Eclipse\Sumo\bin\netgenerate.exe","--grid","--grid.number","3",
  "--grid.length","180","--grid.attach-length","140","--tls.guess","--default.lanenumber",os.environ.get("LANES","2"),
  "--output-file","ab.net.xml", *cfg], check=True, capture_output=True)
traci.start([r"C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe","-n","ab.net.xml","-r","demand.rou.xml",
             "--end","200","--time-to-teleport","-1","--no-step-log"])
stuck = 0
for t in range(200): traci.simulationStep()
ids = traci.vehicle.getIDList()
for v in ids:
    if traci.vehicle.getSpeed(v) < 0.1: stuck += 1
drained = sum(1 for v in ids if traci.vehicle.getRoadID(v).count(".") < 2 and not traci.vehicle.getRoadID(v).startswith("A0A1"))
print(f"lanes={os.environ.get('LANES','2')} turn={len(cfg)}: on-net={len(ids)} stopped={stuck}")
traci.close()
