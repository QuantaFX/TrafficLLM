"""Everything that talks to, or cleans up after, the model.

Measured facts this file encodes (see PROMPTS.md §10): json_schema + strict validates,
structured_outputs.choice is silently ignored, max_tokens must exceed the schema, and
dropping the `why` field saves ~0.9 s per call.
"""
import asyncio
import json
import os
import random
import time

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

from core.contract import Intent, NodeObs

URL = os.environ.get("LLM_URL", "https://llmrouter.boyemma.com/v1/chat/completions")
KEY = os.environ.get("LLM_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "flash")
MAX_TOKENS = 110
TIMEOUT = 45.0
TICK_BUDGET = 22.0

NODE_SYS = (
    "You control one intersection. Each tick pick the movement with the most traffic queued "
    "to get through it and the most room to receive it.\n"
    "hold keeps the current green; switch serves a different movement. Prefer switching when "
    "another movement is clearly worse-off than the current one, and respect min-green.\n"
    "An out-arc with spill 1 or 2 is filling up - do not feed it unless every other movement is "
    "worse. If a neighbour reports a high spill, drain toward it only when its out-arc is clear."
)

ORCH_SYS = (
    "You control a district of a traffic grid that has deadlocked: every road in a cycle is full, "
    "so nothing drains. Pick the smallest sequence of interventions that breaks the cycle.\n"
    "Break the cheapest arc: the shortest blocked queue with the most upstream room. Order matters - "
    "free the node that the most others depend on first. Meter the feeders refilling a cycle arc so it "
    "stays clear while you drain. Never plan more than 6 steps; you are re-called in 2 s."
)

STALL_TIMEOUT_TICKS = 3
STALL_DROP_TICKS = 6


def node_schema(eligible: list[int]) -> dict:
    return {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["hold", "switch"]},
        "target": {"type": "integer", "enum": eligible},
        "green": {"type": "integer", "enum": [5, 10, 15, 20, 30]},
        "why": {"type": "string", "maxLength": 120}},
        "required": ["action", "target", "green", "why"], "additionalProperties": False}


def _src(edge: str) -> str:
    """Readable origin label: the node the approach comes from."""
    parts = edge.lstrip("e").split("_")
    return parts[0][:4] if parts else edge


def digest(o: NodeObs, press: dict[int, int] | None = None) -> str:
    """Compact but legible: one row per approach, pressure only where non-zero."""
    q = " ".join(f"{_src(a.edge)}:{min(a.queue, 9)}" for a in o.approaches)
    sp = max((x.spillback for x in o.outgoing), default=0)
    pr = " ".join(f"P{k}={v}" for k, v in (press or {}).items() if v)
    return (f"{o.name} | queue {q} | spill {sp} | "
            f"phase=P{o.phase} green={o.in_green} min={o.min_green} | press {pr or '-'}")
# NB: dicts preserve discovery order, which is stable per junction


def _body(o: NodeObs, press=None) -> dict:
    return {"model": MODEL, "temperature": 0, "max_tokens": MAX_TOKENS,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "act", "strict": True,
                                                "schema": node_schema(o.eligible or [o.phase])}},
            "messages": [{"role": "system", "content": NODE_SYS},
                         {"role": "user", "content": digest(o, press)}]}


class LLM:
    """One client, all nodes in flight at once, per-node timeout inside the tick budget."""

    def __init__(self, enabled=True):
        self.enabled = enabled and bool(KEY)
        self.client = httpx.AsyncClient(timeout=TIMEOUT,
                                        headers={"Authorization": f"Bearer {KEY}"} if KEY else {})
        self.stalls: dict[str, int] = {}
        self.last: dict[str, Intent] = {}
        self.calls = 0
        self.failures = 0

    def _dead(self, node, err=""):
        self.stalls[node] = self.stalls.get(node, 0) + 1
        self.failures += 1
        prev = self.last.get(node) or Intent(node, "hold", -1, 10)
        return Intent(node, prev.action, prev.target, prev.green,
                      f"no answer ({err})" if err else "no answer",
                      "stall", 0.0, self.stalls[node])

    async def _one(self, o: NodeObs, press=None) -> Intent:
        t0 = time.perf_counter()
        for attempt in (1, 2):
            try:
                r = await asyncio.wait_for(self.client.post(URL, json=_body(o, press)), TICK_BUDGET)
                d = json.loads(r.json()["choices"][0]["message"]["content"])
                self.calls += 1
                self.stalls[o.node] = 0
                i = Intent(o.node, d["action"], int(d["target"]), int(d["green"]),
                           d.get("why", "")[:120], "", time.perf_counter() - t0)
                self.last[o.node] = i
                return i
            except Exception as e:
                last = "timeout" if isinstance(e, asyncio.TimeoutError) else type(e).__name__
                if attempt == 2:
                    break
        return self._dead(o.node, last)

    async def tick(self, obs: list[NodeObs], press: dict[str, dict[int, int]] | None = None) -> list[Intent]:
        if not self.enabled:
            return [Intent(o.node, "hold", -1, 10, "llm off", "parked") for o in obs]
        live = [o for o in obs if not o.parked and self.stalls.get(o.node, 0) < STALL_DROP_TICKS]
        parked = [o for o in obs if o not in live]
        press = press or {}
        done = await asyncio.gather(*(self._one(o, press.get(o.node)) for o in live)) if live else []
        out = [faça(o, i, o.last_switch, o.t, press.get(o.node)) for o, i in zip(live, done)]
        for o in parked:
            i = self.last.get(o.node) or Intent(o.node, "hold", -1, 10)
            out.append(Intent(o.node, i.action, i.target, i.green, "stalled - held", "stall",
                              0.0, self.stalls.get(o.node, STALL_DROP_TICKS)))
        return out

    async def orchestrate(self, report: str) -> dict:
        body = {"model": MODEL, "temperature": 0, "max_tokens": 240,
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "plan", "strict": True, "schema": {
                        "type": "object", "properties": {
                            "diagnosis": {"type": "string", "maxLength": 160},
                            "cycle": {"type": "array", "items": {"type": "string"}},
                            "steps": {"type": "array", "items": {"type": "object", "properties": {
                                "node": {"type": "string"},
                                "act": {"type": "string", "enum": ["escape_phase", "meter", "hold"]},
                                "until": {"type": "string"}},
                                "required": ["node", "act", "until"], "additionalProperties": False}},
                            "release_order": {"type": "array", "items": {"type": "string"}}},
                        "required": ["diagnosis", "cycle", "steps", "release_order"],
                        "additionalProperties": False}}},
                "messages": [{"role": "system", "content": ORCH_SYS},
                             {"role": "user", "content": report}]}
        try:
            r = await asyncio.wait_for(self.client.post(URL, json=body), TIMEOUT)
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            return {"diagnosis": f"orchestrator unavailable ({type(e).__name__})", "cycle": [],
                    "steps": [], "release_order": []}

    async def aclose(self):
        await self.client.aclose()


# ---------------------------------------------------------------- façade


def faça(o: NodeObs, i: Intent, last_switch: int, t: int, phase_pressure=None) -> Intent:
    """Validate an intent against the sim. Never raises; always returns an applicable intent."""
    if i.applied:                        # parked / stalled / stale: nothing to validate
        return i
    i.green = _clamp(i.green, o.min_green, o.max_green)
    if i.action != "switch":
        i.target = o.phase
        i.applied = "ok"
        return i
    if i.target == o.phase:
        i.action, i.applied = "hold", "façade:same-phase"
        return i
    if o.in_green < o.min_green:
        i.action, i.target = "hold", o.phase
        i.applied = "façade:early"
        return i
    if i.target not in o.eligible:
        if not o.eligible:
            i.action, i.target = "hold", o.phase
            i.applied = "façade:early"
            return i
        pick = phase_pressure or {}
        i.target = max(o.eligible, key=lambda p: pick.get(p, 0))
        i.applied = "façade:illegal"
    elif t - last_switch < 2:
        i.action, i.target = "hold", o.phase
        i.applied = "façade:thrash"
    else:
        i.applied = "ok"
    return i


def _clamp(v, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(v), hi))
    except (TypeError, ValueError):
        return lo
