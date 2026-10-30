"""The simulation. Owns SUMO, the signal state machine and the metrics.

Everything here is a pure function of the sim state, so any guard firing can be replayed
from the log. No LLM code in this file.
"""
import json
import os
import time

import libsumo as traci

from core import contract as C
from core import phases as P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMO_BIN = os.environ.get("SUMO_BIN", r"C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe")
SPILL_FULL = 0.9
SPILL_TIGHT = 0.6


class Env:
    def __init__(self, scenario="baseline", seed=42, tls_ttl=None):
        self.scenario = scenario
        self.seed = seed
        self.nodes: dict[str, dict] = {}
        self.tables: dict[str, list[C.Phase]] = {}
        self.sig: dict[str, dict] = {}
        self.started = False
        self.t = 0
        self.speed = 2.0
        self.paused = False
        self.district: C.District | None = None
        self.grants: dict[str, C.Grant] = {}
        self.stats_hist: list[dict] = []
        self._names = NAMES

    # ------------------------------------------------------------ lifecycle

    def start(self):
        net = os.path.join(ROOT, "assets", "taft.net.xml")
        rou = os.path.join(ROOT, "assets", "demand.rou.xml")
        if not os.path.exists(net):
            raise FileNotFoundError(f"missing {net}; run assets/buildnet.py")
        traci.start([SUMO_BIN, "-n", net, "-r", rou, "--seed", str(self.seed),
                     "--time-to-teleport", "-1", "--no-step-log", "--no-warnings",
                     "--collision.action", "none", "--step-length", "1",
                     "--device.tripinfo.probability", "1.0",
                     "--tripinfo-output", os.path.join(ROOT, "logs", "tripinfo.xml")])
        self._index()
        self.started = True

    def _index(self):
        pos_of = {n: traci.junction.getPosition(n) for n in traci.junction.getIDList()}
        pos_of = {k: v for k, v in pos_of.items()}
        for j in traci.trafficlight.getIDList():
            links = []
            for lanefrom, laneto, _idx, _a, _b, direction in _controlled_links(j):
                links.append({"frm": _edge_of(lanefrom), "to": _edge_of(laneto),
                              "direction": direction})
            here = dict(pos_of)
            here["@"] = traci.junction.getPosition(j)
            for l in links:                 # key approaches by the edge's far end
                here[l["frm"]] = pos_of.get(traci.edge.getFromJunction(l["frm"]))
            self.tables[j] = P.table_for(j, links, here)
            self.nodes[j] = {
                "id": j, "name": self._names.get(j, j), "links": links,
                "approaches": sorted({l["frm"] for l in links}),
                "outgoing": sorted({l["to"] for l in links}),
                "neighbours": sorted({l["to"] for l in links if l["to"] in NAMES}),
            }
            # stagger the opening phase by junction index, or every node in the district
            # changes at the same second and the whole grid breathes as one block
            main = [p.idx for p in self.tables[j] if p.kind in ("through", "left")]
            pick = main[(sum(map(ord, j)) + self.t) % len(main)] if len(main) > 1 else (main[0] if main else 0)
            self.sig[j] = {"phase": self._set_phase(j, pick), "since": 0, "last": -99,
                           "min_green": 8, "max_green": 22, "green": 0}

    def _set_phase(self, j, idx):
        traci.trafficlight.setRedYellowGreenState(j, self.tables[j][idx].state)
        return idx

    def _first_green(self, j):
        for p in self.tables[j]:
            if p.green:
                return self._set_phase(j, p.idx)
        return 0

    def close(self):
        if self.started:
            try:
                traci.close()
            except Exception:
                pass
            self.started = False

    # ------------------------------------------------------------ step

    def step(self):
        if not self.started:
            return None
        for s in self.sig.values():
            s["green"] += 1
        # Switch every due node together, then let ONE stepping loop carry the whole
        # yellow/all-red chain. force_phase() stepping per node would stack the chains
        # (9 nodes x 5 s = 45 s of clock per tick).
        due = [j for j, s in self.sig.items() if s["green"] >= s["max_green"]]
        if due:
            self.switch(due, {j: self._next_through(j) for j in due})
        traci.simulationStep()
        self.t = int(traci.simulation.getTime())
        return self.frame()

    def switch(self, nodes, targets):
        """Yellow -> all-red -> green for several junctions in one shared stepping loop."""
        tables = {j: self.tables[j] for j in nodes}
        n = len(next(iter(self.nodes.values()))["links"]) if False else None
        for s_, dur in (("yellow", C.YELLOW), ("allred", C.ALL_RED)):
            for j in nodes:
                st = tables[j][self.sig[j]["phase"]].state
                if s_ == "yellow":
                    st = st.replace("G", "y")
                else:
                    st = "r" * len(st)
                traci.trafficlight.setRedYellowGreenState(j, st)
            for _ in range(dur):
                traci.simulationStep()
                self.t = int(traci.simulation.getTime())
        for j in nodes:
            traci.trafficlight.setRedYellowGreenState(j, tables[j][targets[j]].state)
            s = self.sig[j]
            s["phase"], s["since"], s["last"], s["green"] = targets[j], self.t, self.t, 0

    def force_phase(self, j, idx, yellow=True):
        """Hard switch. Returns seconds the yellow/all-red chain takes."""
        s, tab = self.sig[j], self.tables[j]
        if idx == s["phase"]:
            s["green"] = 0
            return 0
        cost = 0
        if yellow:
            cur = tab[s["phase"]].state
            for s_, dur in ((cur.replace("G", "y").replace("g", "g"), C.YELLOW),
                            ("r" * len(cur), C.ALL_RED)):
                traci.trafficlight.setRedYellowGreenState(j, s_)
                for _ in range(dur):
                    traci.simulationStep()
                    self.t = int(traci.simulation.getTime())
                cost += dur
        traci.trafficlight.setRedYellowGreenState(j, tab[idx].state)
        s["phase"], s["since"], s["last"], s["green"] = idx, self.t, self.t, 0
        return cost

    def _next_through(self, j):
        """Rotate the main phases only. Escape phases are entered explicitly, by the
        orchestrator or by an agent switching into one."""
        tab, cur = self.tables[j], self.sig[j]["phase"]
        order = [p.idx for p in tab if p.kind in ("through", "left")]
        if not order:
            return cur
        if cur in order:
            nxt = order[(order.index(cur) + 1) % len(order)]
        else:
            nxt = order[0]
        return nxt

    # ------------------------------------------------------------ observation

    def _lane_stats(self, edge):
        q, cap = [], []
        for lane in (f"{edge}_{i}" for i in range(traci.edge.getLaneNumber(edge))):
            ln = traci.lane.getLastStepVehicleNumber(lane)
            length = traci.lane.getLength(lane)
            q.append(ln)
            cap.append(max(0, int(length / 7.5) - ln))
        return q, cap

    def _out(self, edge):
        q, cap = self._lane_stats(edge)
        occ, room = sum(q), sum(q) + sum(cap)
        fill = occ / max(1, room)
        return C.OutLink(edge, occ, room, 2 if fill >= SPILL_FULL else 1 if fill >= SPILL_TIGHT else 0)

    def eligible(self, j):
        """Phases in the action enum.

        Includes the current phase so the model can answer "serve this one" at any time;
        the façade is what enforces min-green by converting an early switch to a hold.
        """
        return [p.idx for p in self.tables[j] if p.green]

    def obs(self, j) -> C.NodeObs:
        n, s = self.nodes[j], self.sig[j]
        g = self.grants.get(j)
        return C.NodeObs(
            node=j, name=n["name"], phase=s["phase"], in_green=s["green"],
            min_green=s["min_green"], max_green=s["max_green"],
            approaches=[C.Approach(e, *self._lane_stats(e), 0) for e in n["approaches"]],
            outgoing=[self._out(e) for e in n["outgoing"]],
            neighbours=[{"node": k, "name": self.nodes[k]["name"], "phase": self.sig[k]["phase"],
                         "queue": sum(sum(self._lane_stats(e)[0]) for e in self.nodes[k]["approaches"]),
                         "spillback": max(self._out(e).spillback for e in self.nodes[k]["outgoing"])}
                        for k in n["neighbours"]],
            eligible=self.eligible(j), tickets=[],
            locked_by=g.id if g else "", parked=bool(g),
            last_switch=s["last"], t=self.t)

    def obs_all(self):
        return [self.obs(j) for j in self.nodes]

    def all_obs(self):
        return {j: self.obs(j) for j in self.nodes}

    # ------------------------------------------------------------ gridlock

    def blocked_edges(self):
            """(from, to) arcs this junction's outbound edge spills back onto.

            An outbound edge runs FROM this junction, so the arc is (j -> edge's tail node).
            """
            out, seen = [], set()
            for j, n in self.nodes.items():
                for e in n["outgoing"]:
                    if e in seen or "_" not in e:
                        continue
                    seen.add(e)
                    tail = e.split("_")[-1]
                    if tail != j and self._out(e).spillback >= 2:
                        out.append((j, tail))
            return out

    def cycle(self):
        """Simplest cycle of >=3 nodes with every arc blocked, or []."""
        adj = {}
        for a, b in self.blocked_edges():
            adj.setdefault(a, set()).add(b)
        for start in adj:
            stack = [(start, [start])]
            while stack:
                cur, path = stack.pop()
                for nxt in adj.get(cur, ()):
                    if nxt == start and len(path) >= 3:
                        return path
                    if nxt not in path and len(path) < 8:
                        stack.append((nxt, path + [nxt]))
        return []

    # ------------------------------------------------------------ metrics

    def stats(self):
        try:
            ids = traci.vehicle.getIDList()
        except Exception:                 # unit tests build an Env without SUMO
            ids = []
        if ids:
            speeds = [traci.vehicle.getSpeed(v) for v in ids]
            waits = [traci.vehicle.getAccumulatedWaitingTime(v) for v in ids]
            arrived = traci.simulation.getArrivedNumber()
        else:
            speeds, waits, arrived = [0], [0], 0
        return {
            "t": self.t,
            "veh": len(ids),
            "arrived": arrived,
            "throughput": arrived / max(1, self.t) * 3600,
            "speed": sum(speeds) / len(speeds),
            "wait": sum(waits) / len(waits),
            "blocked": len(self.blocked_edges()),
            "in_district": len(self.grants),
        }

    # ------------------------------------------------------------ frame

    def frame(self, intents=None):
        nodes = []
        for j, n in self.nodes.items():
            s = self.sig[j]
            nodes.append({
                "id": j, "name": n["name"], "phase": s["phase"], "green": s["green"],
                "locked": j in self.grants,
                "pressure": max((self.obs(j).pressure(self.tables[j][s["phase"]]), 0)),
                "queues": [sum(self._lane_stats(e)[0]) for e in n["approaches"]],
                "spill": max((self._out(e).spillback for e in n["outgoing"]), default=0),
            })
        veh = [{"id": v, "x": float(traci.vehicle.getPosition(v)[0]),
                "y": float(traci.vehicle.getPosition(v)[1]),
                "a": float(traci.vehicle.getAngle(v))} for v in traci.vehicle.getIDList()]
        edges = []
        for e in traci.edge.getIDList():
            if e.startswith(":"):
                continue
            q, cap = self._lane_stats(e)
            room = sum(q) + sum(cap)
            edges.append({"id": e, "fill": round(sum(q) / max(1, room), 2)})
        d = None
        if self.district:
            d = {"id": self.district.id, "nodes": self.district.nodes,
                 "cycle": self.district.cycle, "gen": self.district.gen}
        return C.Frame(self.t, nodes, veh, edges, d, self.stats(), intents or [])


NAMES = {
    "A11": "Taft Ave & Vito Cruz (DLSU Main Gate)",
    "A12": "Taft Ave & Pablo Ocampo (DLSU South Gate)",
    "A13": "Taft Ave & Quirino Ave",
    "B11": "Vito Cruz & Apacible",
    "B12": "Pablo Ocampo & Apacible",
    "B13": "Quirino & Apacible",
    "C11": "Vito Cruz & Leon Guinto",
    "C12": "Pablo Ocampo & Leon Guinto",
    "C13": "Quirino & Leon Guinto",
    "A01": "Taft Ave (South approach)",
    "A21": "Taft Ave (North approach)",
    "B10": "Vito Cruz (West approach)",
    "B30": "Vito Cruz (East approach)",
    "C01": "Leon Guinto (South approach)",
    "C21": "Leon Guinto (North approach)",
    "A10": "Taft Ave & Apacible South",
    "A20": "Taft Ave & Apacible North",
    "B20": "Apacible & Leon Guinto North",
    "B21": "Pablo Ocampo West",
    "B22": "Apacible East",
    "A00": "Taft Ave (South Boundary)",
    "A02": "Taft Ave (North Boundary)",
    "B00": "Vito Cruz (West Boundary)",
    "B01": "Vito Cruz (Central West)",
    "B02": "Vito Cruz (West-North)",
    "C00": "Leon Guinto (South Boundary)",
    "C02": "Leon Guinto (North Boundary)",
    "C10": "Leon Guinto South",
    "C20": "Leon Guinto North",
    "C22": "Leon Guinto & Quirino",
}


def _edge_of(lane: str) -> str:
    return lane.rsplit("_", 1)[0] if "_" in lane else lane


def _controlled_links(j):
    """[(from_lane, to_lane, link_index, None, None, direction)] in link order.

    libsumo returns getControlledLinks as (fromLaneID, toLaneID, viaLaneID) string triples.
    """
    out = []
    for group in traci.trafficlight.getControlledLinks(j):
        for frm, to, idx in group:
            # A lane can hold several connections; the direction belongs to the one that
            # leads to `to`, not to links[0] (all of lane 2 would read as a left turn).
            links = traci.lane.getLinks(frm)
            direction = next((l[6] for l in links if l[4] == idx or l[0] == to), None)
            direction = direction or (links[0][6] if links else "t")
            out.append((frm, to, idx, None, None, direction))
    return out