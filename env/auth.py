"""Control-authorisation layer.

Node agents keep deciding while the orchestrator holds a district; their output is staged
and applied only if the node is still theirs. Grants carry a `gen` so a late answer from a
previous generation cannot land. All functions are pure over the grant dict so a takeover
race replays exactly from the log.
"""
from core.contract import Grant

GATE_SPILL = 1
GATE_CLEAN_TICKS = 2


class Auth:
    def __init__(self):
        self.gen = 0
        self.grants: dict[str, Grant] = {}
        self.clean: dict[str, int] = {}

    def lock(self, nodes: list[str], phase_of, until="depth_le_1", constraints=()):
        self.gen += 1
        for n in nodes:
            self.grants[n] = Grant(f"{n}@g{self.gen}", self.gen, n, phase_of[n], until,
                                   list(constraints))
            self.clean[n] = 0

    def belongs(self, node: str, gen: int) -> bool:
        g = self.grants.get(node)
        return bool(g and g.gen == gen)

    def release(self, node: str):
        """Hand back. Caller must have checked the gate."""
        return self.grants.pop(node, None)

    def gate(self, node: str, spill: int, ok: bool) -> bool:
        """Two consecutive clean ticks plus spill back to nominal."""
        self.clean[node] = self.clean.get(node, 0) + 1 if (ok and spill <= GATE_SPILL) else 0
        return self.clean[node] >= GATE_CLEAN_TICKS

    def release_all(self):
        self.grants.clear()
        self.clean.clear()

    def note(self, i, gen_when_asked: int):
        """Stamp an intent with its application state. `parked` if it lost the race."""
        g = self.grants.get(i.node)
        if g and g.gen != gen_when_asked:
            i.applied = "façade:stale-grant"
            i.target = g.phase
        elif g:
            i.applied = "façade:granted"
            if i.action == "switch":
                i.target, i.action = g.phase, "hold"
        else:
            i.applied = "ok"
        return i