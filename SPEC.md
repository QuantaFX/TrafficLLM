# TrafficLLM — finalised project spec

**One line:** a traffic-light simulation where every intersection is driven by its own LLM agent,
and a supervising Orchestrator LLM seizes control of a jammed district, breaks the deadlock, and
hands the intersections back.

Target: **portfolio demo that runs.** Not a paper. Every choice below optimises for
"impressive and demonstrable", and says where that costs rigour.

---

## 1. Premise

Traffic gridlock is a cycle of full road links: every arc in the cycle is occupied to capacity,
so nothing in the cycle can move and nothing empties. Point-queue control theory is blind to it
(Varaiya 2013 §4.5: the model "does not permit saturation"), and the LLM traffic-control
literature barely touches it — 2 arXiv hits for *gridlock* + *traffic signal*, one relevant
paper, no LLM.

This project puts an LLM on every intersection and gives it only local arcs to see, then asks
what happens when nine locally-sensible agents deadlock each other — and what a supervisory
agent with a district-wide view can do about it.

## 2. Actors

| actor | sees | decides |
|---|---|---|
| **Node agent** (one per intersection) | occupancy and queue on its own in-arcs and out-arcs, plus neighbour digests | phase, and hold-vs-switch |
| **Orchestrator** | the spillback relation over the whole grid | takeover, escape phases, upstream metering, hand-back |

Agents are LLMs. There is no rule controller underneath — every phase decision in the running
system is an LLM decision. (Consequence: a slow or dead model visibly freezes the light. That is
the risk, and on a demo it is also the story.)

**Node agent**, per tick:
- observe own arcs → `up[i]`, `dn[i]` per movement
- pressure `p(a) = max(0, up(a) − dn(a))` per movement, phase pressure = max over its movements
- read neighbour digests (fixed schema, one line each)
- emit exactly one action through constrained decoding:
  `hold` | `switch:<PHASE_ID>` | `extend:<5|10|15|20|30>`
- phase and duration are both LLM-chosen; the validity façade only rejects illegal output

**Orchestrator**, on trigger: locks the affected district (`who = orchestrator`), inserts an
escape phase at the closing node, meters the upstream feeds that keep re-filling the cycle, works
the district until it drains, then hands back node by node as each passes its gate.

## 3. Control loop

**Measured on the live endpoint, 9 concurrent node calls: 4.58 s wall, 4.12 s median per call,
9/9 schema-valid.** That sets the tick.

- Sim step **1 s**, agent decision tick **5 s** (a ~4.6 s tick fits it), orchestrator tick **2 s**
  while in control.
- Calls go out **concurrently**, not in a sequential fan-out: one HTTP request per node, all nine
  in flight at once, parsed as they land. Sequential would be ~12 s and blow the tick.
- Because it is LLM-only, the loop **waits** on the model. The tick is a hard latency budget, not
  best-effort, and the sim clock does not advance until the batch resolves.
- 5×5 will not fit a 5 s tick at this latency (25 nodes). Coarsen to 10 s, or run a single
  batched call carrying 25 digests — decide at scale, but do not discover it live.
- **Agent actions mirror back instantly** — each intent is annotated on the intersection in the
  UI the moment it lands, and only then does the phase change. The reasoning is the visible
  artefact, not a log line.

## 4. Orchestrator trigger and escape

**Trigger (deterministic detector, not an LLM noticing):** build the subgraph of arcs at
`occupancy ≥ 0.9`; find a cycle of ≥ 3 nodes where every arc is blocked; require it to persist
2 ticks. Snapshot the district, set `gen += 1`.

**Escape, in order:**
1. Insert a dedicated escape phase at the node whose downstream arc is most blocked.
2. Meter the upstream feeds refilling the cycle (cap inflow, forbid the closing movement).
3. Walk outward one ring at a time; re-detect. If the cycle shrinks, keep going; if it does not
   shrink after 3 attempts, widen the cordon and retry (escape hatch against live-lock).
4. Hand back node by node when the gate passes.

**Hand-back gate**, all on one tick: spillback depth ≤ 1 · inbound queue ≤ baseline + 1σ ·
drain objective met · two clean ticks · constraints restored. The hand-back message re-states the
held constraints verbatim (compressed handoffs drop constraints in 97% of cases — arXiv
2608.24569), and the gate is checked independently of the message.

## 5. Failure guards (LLM-only makes these load-bearing)

| failure | guard |
|---|---|
| hallucinated phase | `structured_outputs.choice` over currently-green-eligible phases only |
| invalid duration / min-green violation | façade converts to `hold` and logs a façade-hit |
| silent or slow model | last action holds; staleness counter; at ≥ 3 ticks a fresh stateless call, at ≥ 6 the LLM's last known good phase is frozen and the node is flagged **stalled in the UI** |
| thrashing | min-green 8 s, max one switch per 2 ticks, `A→B→A` inside 45 s counted as a failed decision and shown in the metrics |
| oscillation between neighbours | one priority ticket per node, two lit tickets → drain wins |
| takeover race | control-grant record; agents still think and emit, output is staged and applied only if `gen` matches |

Every guard is a pure function of digest fields, so any firing replays exactly from the log.

## 6. Scenario

- **Dev:** sumo-rl `3x3Grid2lanes.net.xml` (MIT), 9 signals, turn-heavy demand at 1,500–2,000 veh/h
  — the shipped demand peaks at 40 vehicles and cannot gridlock.
- **Gridlock fixture:** two-arc ring + narrow links + ramp gore + one-way segment, generated as
  route files, so every run reaches a full queue on a closing link.
- **Scale:** 5×5 after the demo works; cadence coarsens to 10 s and the detector batches.
- `--time-to-teleport -1` is mandatory — SUMO otherwise teleports jams away after 300 s.

## 7. Stack

SUMO 1.28 + `libsumo` in-process (measured 1,705 steps/s with full observation vs 109 on traci —
the socket protocol costs 194× per read, and an LLM-only loop cannot afford to pay IPC as well).
One process per episode; threads only for the LLM service. Plain in-process function calls for
the orchestrator, no agent framework.

**Model: `flash`** on `https://llmrouter.boyemma.com/v1` (key in `.env` as
`HERMES_CUSTOM_PANDEV_API_KEY`). Note what this endpoint does: `structured_outputs.choice` is
accepted but **not enforced** — the model echoes your input instead of picking from the mask. Use
`response_format: json_schema` with `strict: true`, which does validate (`a` stayed in its enum
across runs). The façade stays mandatory because the schema is permissive about *semantics*
(`"phase": <any string>`), so a well-formed wrong phase is still possible.

Two hard rules, both consequences of the endpoint: **the system prompt is resent on every call**,
so keep it short and put the static part first (it is cache-friendly); and retry at most once per
node per tick before the stall guard takes over.

Warm-model latency after a first call drops to ~1.2 s. Cold ~2.1 s, nine-way concurrent ~4.6 s.
Keep the model warm between episodes.

## 8. Deliverables

1. Running simulation with live map: intersections, queues, arcs colouring by occupancy.
2. Agent intent feed — every node's chosen phase, one-line reason, current pressure; façade hits
   and stalls in a different colour.
3. Orchestrator panel: detection, the locked district, escape steps, hand-back.
4. Metrics: avg wait, avg travel time, throughput, queue length, blocked-arc count,
   LLM call rate, façade-hit rate, switch-thrash count. AWT and blocked arcs always visible.
5. Comparators so the numbers mean something: fixed-time, max-pressure, and a no-orchestrator
   ablation (all deterministic, no LLM).
6. The three failure clips: a gridlock forming, the orchestrator breaking it, and a model stall
   recovered by the guard.

Speed control, pause, restart, deterministic seed. Demo-recordable, no setup during recording.

## 9. Where it is exposed

- **An LLM-only lower layer will lose to max-pressure on raw AWT.** Own it: compare against it
  and frame the result as behaviour and explainability against two strong cheap baselines.
- **Published LLM-TSC work runs one call per phase (15–30 s).** A 5 s LLM tick is unusual, so
  report the tick latency you actually achieve; if it does not fit, coarsen to 10 s rather than
  drop to a rule controller by accident.
- **Gridlock must actually occur** — count and report blocked-arc events per run, or the demo is
  just "LLM agents redo max-pressure".

## 10. Build order

1. Export headless metrics for the comparators; confirm the fixture gridlocks (≥ 3 failed runs
   before tuning).
2. Node agent loop with audiences and façade on mission — nine nodes, 5 s tick.
3. Orchestrator: detector → lock → escape → gate → hand-back.
4. UI, intent feed, stall/thrash surfaces.
5. Scale to 5×5, Pause 6 and 8 release if reachable in the time left.

## 11. Open, to settle during M1

- Model choice (needs the throughput measurement first).
- Whether 5 s survives 9 nodes on the real GPU, or the tick must be 10 s.
- Whether the escape phases need to be hand-authored per junction type, or generated from the
  phase table.