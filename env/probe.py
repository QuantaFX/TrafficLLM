import libsumo as traci
traci.start([r"C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe","-n","taft.net.xml","-r","demand.rou.xml","--end","200","--no-step-log"])
for t in range(200):
    traci.simulationStep()
    if t in (0,5,20,60,150):
        print(t, "loaded", traci.simulation.getLoadedNumber(), "inserted", traci.simulation.getDepartedNumber(),
              "waiting", traci.vehicle.getIDCount() and len(traci.simulation.getMinExpectedNumber and []), "ids", traci.vehicle.getIDList()[:5])
traci.close()
