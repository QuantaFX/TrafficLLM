"""Fixed contract shared by env, LLM layer, UI and tests. No project imports here.

Everything crossing a module boundary is one of these shapes. If a field is needed by
two modules it belongs here, not in a private dict.
"""
from dataclasses import dataclass, field, asdict
from typing import Any

# ---------------------------------------------------------------- phases

# Signal states, one char per connection in the junction's link order.
# 'G' = green, 'g' = permissive green, 'y' = yellow, 'r' = red.
YELLOW = 3
ALL_RED = 2


@dataclass(frozen=True)
class Phase:
    idx: int
    state: str
    movements: tuple[str, ...]          # human label per served movement
    kind: str                           # 'through' | 'left' | 'right' | 'escape' | 'allred'

    @property
    def green(self) -> bool:
        return self.kind != "allred"


# ---------------------------------------------------------------- observation


@dataclass
class Approach:
    """One inbound edge, lanes ordered 0 = leftmost."""
    edge: str
    queues: list[int]                   # vehicles per lane
    capacity: list[int]                 # free slots per lane
    outflow: int                        # vehicles per hour that left via this edge

    @property
    def queue(self) -> int:
        return sum(self.queues)

    @property
    def deepest(self) -> int:
        return max(self.capacity) - min(self.capacity)


@dataclass
class OutLink:
    edge: str
    occupancy: int
    capacity: int
    spillback: int                      # 0 none .. 2 full

    @property
    def fill(self) -> float:
        return self.occupancy / max(1, self.capacity)


@dataclass
class NodeObs:
    """One intersection's local view."""
    node: str
    name: str
    phase: int
    in_green: int
    min_green: int
    max_green: int
    approaches: list[Approach]
    outgoing: list[OutLink]
    neighbours: list[dict]              # [{node,name,phase,queue,spillback}]
    eligible: list[int]                 # legal switch targets right now
    tickets: list[str]
    locked_by: str = ""                 # '' | orchestrator grant id
    parked: bool = False                # frozen by the authorisation layer
    last_switch: int = -99              # sim second of the previous phase change
    t: int = 0                          # sim second now

    def pressure(self, ph: Phase) -> int:
        """Pooled queue minus pooled outflow room, floored at 0.

        Not Varaiya's: he forbids saturation, this penalises it deliberately.
        Movement keys are 'turn:<inbound edge>' so a phase can be joined to its lanes.
        """
        ups, downs = [], []
        for m in ph.movements:
            _, _, src = m.partition(":")
            ups += [a for a in self.approaches if a.edge == src]
            downs += [o for o in self.outgoing if o.edge in src or src in o.edge]
        return max(0, sum(a.queue for a in ups) - sum(int(o.fill * 10) for o in downs))


# ---------------------------------------------------------------- intents


@dataclass
class Intent:
    """What an agent decided. `applied` is set by the façade, not the agent."""
    node: str
    action: str                         # 'hold' | 'switch'
    target: int                         # phase idx, or -1 for hold
    green: int                          # requested green seconds
    why: str = ""
    applied: str = ""                   # 'ok' | 'façade:<reason>' | 'stall' | 'parked'
    latency: float = 0.0
    stall: int = 0                      # consecutive ticks without an answer

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- orchestrator


@dataclass
class District:
    id: str
    nodes: list[str]
    cycle: list[str]
    blocked: list[tuple[str, str]]
    gen: int
    started: int


@dataclass
class EscapePlan:
    diagnosis: str
    cycle: list[str]
    steps: list[dict]                   # [{node, act, phase, until}]
    release_order: list[str]


@dataclass
class Grant:
    """Control record for a locked node. Applied per tick, so a late answer is safe."""
    id: str
    gen: int
    node: str
    phase: int
    until: str
    constraints: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- frames


@dataclass
class Frame:
    """One rendered simulation frame. The UI renders this and nothing else."""
    t: float
    nodes: list[dict]                   # element dict per intersection
    vehicles: list[dict]                # sparse, for electric update
    edges: list[dict]                   # congestion colouring
    district: dict | None
    stats: dict
    intents: list[dict]


@dataclass
class Controls:
    speed: float = 2.0
    paused: bool = False
    seed: int = 42
    scenario: str = "baseline"
    llm: bool = True


# ---------------------------------------------------------------- action names

ACTIONS = {"hold", "switch"}
FAÇADE_REASONS = ("early", "illegal", "unknown-phase", "bad-duration", "thrash", "stall", "parked")