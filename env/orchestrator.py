"""Orchestrator: detect, lock, escape, gate, hand back.

The detector is deterministic; the plan comes from the model. Escape phases are inserted
as real signal phases at the closing node, feeders are metered, and each node is released
only when its gate passes - checked independently of anything the model says.
"""
import asyncio

from core.contract import District
from env.auth import Auth

PERSIST_TICKS = 2
WIDEN_AFTER = 3
TICK = 2


class _Done:
    def done(self):
        return True


class Orchestrator:
    def __init__(self, env, llm):
        self.env, self.llm = env, llm
        self.auth = Auth()
        self.d = None
        self.cycle_age = 0
        self.attempts = 0
        self.log: list[dict] = []
        self.pending = None
        self.planned = False      # plan finished, gate loop is live

    # ------------------------------------------------------------ detection

    def detect(self):
        if self.d is not None:
            return
        cyc = self.env.cycle()
        self.cycle_age = self.cycle_age + 1 if cyc else 0
        if self.cycle_age >= PERSIST_TICKS:
            self.begin(cyc)

    def begin(self, cyc):
        self.d = District(f"d{self.auth.gen + 1}", list(cyc), list(cyc), self.env.blocked_edges(),
                          self.auth.gen + 1, self.env.t)
        self.env.district = self.d
        self.attempts = 0
        self.planned = False
        self._emit("detect", f"deadlock on {len(cyc)} nodes, cycle {' -> '.join(cyc)}")

    # ------------------------------------------------------------ plan

    def _report(self) -> str:
        rows = []
        for n in self.d.nodes:
            o = self.env.obs(n)
            deepest = max(o.approaches, key=lambda a: a.queue, default=None)
            rows.append(f"{n} phase=P{o.phase} queue={deepest.queue if deepest else 0} "
                        f"spill={max((x.spillback for x in o.outgoing), default=0)}")
        return (f"DISTRICT lock={','.join(self.d.nodes)} gen={self.d.gen} tick={self.env.t}\n"
                f"detector: cycle {' -> '.join(self.d.cycle)}, every arc >=0.9 occupancy\n"
                + "\n".join(rows))

    async def plan(self):
        p = await self.llm.orchestrate(self._report())
        steps = [s for s in p.get("steps", []) if s.get("node") in self.env.nodes]
        order = [n for n in p.get("release_order", []) if n in self.env.nodes] or list(self.d.nodes)
        self.auth.lock(self.d.nodes, {n: self.env.sig[n]["phase"] for n in self.d.nodes},
                       constraints=["min_green", "max_green"])

        applied = []
        for s in steps[:6]:
            n = s["node"]
            if s["act"] == "escape_phase":
                tab = self.env.tables[n]
                esc = [x for x in tab if x.kind == "escape"]
                if not esc:
                    continue
                best = max(esc, key=lambda x: sum(a.queue for a in self.env.obs(n).approaches))
                self.env.parked_phase[n] = best.idx
                self._emit("escape", f"{n}: escape phase for {best.movements[0]}")
            elif s["act"] == "meter":
                self._meter(n, s.get("until", ""))
                self._emit("meter", f"{n}: upstream feeds capped")
            applied.append(s)

        self._emit("plan", p.get("diagnosis", "no diagnosis"))
        self._emit("release", "hand-back order: " + " -> ".join(order))
        self.planned = True
        self.d.order = order

    def _meter(self, node, until):
        """Cap the feeds refilling a cycle arc: hold the through phase of upstream nodes."""
        for nb in self.env.nodes[node]["neighbours"]:
            if nb in self.d.nodes:
                continue
            self.auth.lock([nb], {nb: self.env.sig[nb]["phase"]}, until=until or "queue_below_baseline")
            self._emit("meter", f"{nb} metering into {node}")

    # ------------------------------------------------------------ tick

    def step(self):
        """Called each orchestrator tick. Returns True if still controlling."""
        if self.d is None:
            self.detect()
            return False
        if not self.planned:
            if self.pending is None:
                self.pending = self._spawn(self.plan())
                return True
            if not self.pending.done():
                return True
            self.pending = None
            return True

        released = []
        for n in list(self.auth.grants):
            o = self.env.obs(n)
            spill = max((x.spillback for x in o.outgoing), default=0)
            queue = sum(a.queue for a in o.approaches)
            if self.auth.gate(n, spill, queue <= 4):
                g = self.auth.release(n)
                if g:
                    released.append(n)
                    self._hand_back(n, g)

        if not self.auth.grants:
            self.d = None
            self.env.district = None
            self.env.parked_phase.clear()
            self.pending = None
            self.planned = False
            self.cycle_age = 0
            self._emit("done", "district drained, all nodes local")
        elif self.env.cycle() and len(released) == 0:
            self.attempts += 1
            if self.attempts >= WIDEN_AFTER:
                cyc = self.env.cycle()
                for n in cyc:
                    if n not in self.auth.grants:
                        self.auth.lock([n], {n: self.env.sig[n]["phase"]})
                self.d.nodes = list(dict.fromkeys(self.d.nodes + cyc))
                self.env.district.nodes = self.d.nodes
                self.attempts = 0
                self._emit("widen", f"cordon widened to {len(self.d.nodes)} nodes")
        return True

    @staticmethod
    def _spawn(coro):
        """create_task needs a running loop; outside one, run to completion now."""
        try:
            return asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            asyncio.run(coro)
            return _Done()

    def _hand_back(self, n, g):
        held = (f"control returned to {n}. Held by orchestrator: phase=P{g.phase}, "
                f"min_green=8, max_green=45. Drain objective met.")
        self.env.parked_phase.pop(n, None)
        self.auth.clean.pop(n, None)
        self._emit("handback", held)

    def _emit(self, kind, text):
        self.log.append({"t": self.env.t, "kind": kind, "text": text})
        if len(self.log) > 200:
            del self.log[:50]