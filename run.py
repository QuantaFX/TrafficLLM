"""TrafficLLM - an LLM at every intersection of a Taft Ave / DLSU grid, plus an
orchestrator LLM that breaks the gridlock they cause.

    python run.py                 # serve the demo on 127.0.0.1:8800
    python run.py --no-llm        # deterministic, no model calls
    python run.py --scenario gridlock --seed 7

Node agents decide every 5 s, the orchestrator every 2 s, the sim steps every 1 s.
"""
import argparse
import asyncio
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUMO_BIN = os.environ.get("SUMO_BIN", r"C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe")


def assets():
    net = os.path.join(HERE, "assets", "taft.net.xml")
    rou = os.path.join(HERE, "assets", "demand.rou.xml")
    if not os.path.exists(net) or "--force" in sys.argv:
        print("building network ...")
        subprocess.run([sys.executable, os.path.join(HERE, "assets", "buildnet.py")], check=True)
    if not os.path.exists(rou) or "--force" in sys.argv:
        print("building demand ...")
        subprocess.run([sys.executable, os.path.join(HERE, "assets", "build_demand.py")], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--scenario", default="baseline")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--force", action="store_true", help="rebuild net and demand")
    a = ap.parse_args()
    if not os.path.exists(SUMO_BIN):
        sys.exit(f"SUMO not found at {SUMO_BIN}; set SUMO_BIN")
    assets()
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    import server
    print(f"\n  TrafficLLM  http://{a.host}:{a.port}/\n")
    asyncio.run(server.main(a))


if __name__ == "__main__":
    main()