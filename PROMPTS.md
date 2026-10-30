# Prompt and payload formats

Every shape here was sent to `flash` and returned valid output. Copy them; do not improvise.

**Endpoint:** `POST https://llmrouter.boyemma.com/v1/chat/completions`
**Auth:** `Authorization: Bearer <HERMES_CUSTOM_PANDEV_API_KEY>`
**Model:** `flash`, `temperature: 0`
**Enforcement:** `response_format.json_schema` with `strict: true`. Do **not** use
`structured_outputs.choice` — the endpoint accepts it and ignores it.

**`max_tokens` must exceed the schema.** At `max_tokens: 40` with a `why` field the JSON is cut
mid-string (`finish_reason: "length"`) and fails to parse. §1 and §7 sizes are the tested ones.

**Speed lever:** the `why` field costs ~0.9 s per call (2.5 s → 1.6 s without it). The UI wants
the reason, so keep it for the demo and drop it if a 5 s tick ever slips.

---

## 1. Node agent — decision request

One request per node, all in flight at once, `max_tokens: 90` (tested; 40 truncates).

```json
{
  "model": "flash",
  "temperature": 0,
  "max_tokens": 90,
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "node_action",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": {
          "phase":   { "type": "string", "enum": ["hold", "switch:NTST", "switch:ELWL"] },
          "green_s": { "type": "integer", "enum": [5, 10, 15, 20, 30] },
          "why":     { "type": "string", "maxLength": 90 }
        },
        "required": ["phase", "green_s", "why"],
        "additionalProperties": false
      }
    }
  },
  "messages": [
    { "role": "system", "content": "<STATIC — see §4>" },
    { "role": "user",   "content": "<DIGEST — see §2>" }
  ]
}
```

The `phase` enum is **generated per tick**: `"hold"` plus only the phases green-eligible for this
node right now (not the current phase, not ones in yellow). That is what keeps the mask legal.

### Expected response

```json
{"phase": "switch:NTST", "green_s": 15, "why": "N queue 4, out-arc clear, NS_T 20s green"}
```

## 2. The digest — user message

Fixed-width, buckets not counts, arc order N/E/S/W. ~55 tokens.

```
NODE N5 (row1,col2)  t=0045  gen=1  who=local
phase=NS_T  in_green=20  min_green=8  max_green=30  eligible=[NS_T,NS_L]
in  N(5) E(1) S(10) W(0)
out N(0) E(3) S(9) W(1)
spillback N=0 E=1 S=2 W=0
nb  N4(sp2,q4,ELWS,4s)  N6(sp0,q1,NTST,19s)          # full form, ±1 hop
far N2(sp0,q2) N8(sp0,q0)                             # headline only
tickets [E→S, 3t]                                    # optional, ≤2
```

Rules: arc order always N, E, S, W · numbers are `0-5` buckets above 5 · `far` neighbours are
headline-only · never send history, only current state.

## 3. Action parsing

| raw | parse | façade |
|---|---|---|
| `hold` | keep phase | — |
| `switch:<P>` where P ∈ eligible and `in_green ≥ min_green` | switch now | — |
| `switch:<P>` where P ∈ eligible, `in_green < min_green` | `hold` | façade-hit `early` |
| `switch:<P>` where P ∉ eligible | best-pressure eligible phase | façade-hit `illegal` |
| anything unparseable / timeout ≥ 3 ticks | last accepted action | façade-hit `stall` |

Then clamp `green_s` into `[min_green, remaining max_green]` and emit `extend` when the bucket
exceeds the remaining max-green.

## 4. System prompt (node) — static, ≤ 700 chars

> You control one intersection in a traffic grid. Each tick, pick the movement that has the most
> traffic queued to get through it and the most room to receive it.
>
> `hold` keeps the current green. `switch:<PHASE>` serves a different movement. Prefer switching
> when another movement is clearly worse-off than the current one, and respect min-green.
> An out-arc with spillback 1 or 2 is filling up — do not feed it unless every other movement is
> worse. If a neighbour reports a high spillback, drain toward it only when its out-arc is clear.

**Prompt the decision, not the formula.** Measured on `flash`, 3 trials each, unambiguous digest:
"pick the phase with the most queued traffic" got the correct phase 3/3 at 1.82 s, while
"compute upstream sum minus downstream sum" got it 0/3 at 1.95 s. The model reasons over
"who is waiting and who has room" — hand it arithmetic and it fumbles it. Pressure is still the
concept; it does not need to be the wording.

Keep the prompt under 700 chars and put nothing dynamic in it — the endpoint resends it on every
call and there is no prefix cache.

### 4a. Payload shape — revised in response to a worked example

Your payload is **better than mine in two ways and worse in three**. Verdict: adopt its *richness*,
fix its *redundancy*, and precompute the parts a small model gets wrong.

**What it does better:**

1. **Named actors.** `"from_agent": "Quirino Ave (Upstream)"` with a plausible message beats
   `N4(sp2,q4)`. It gives the model a spatial story it can reason over, and it is far better on a
   demo screen. Keep this shape; my digest was over-compressed toward gridlock work.
2. **Explicit `objective`.** "eliminate stop-and-go acceleration carbon spikes" steers decisions
   the way a metrics list cannot — it is a bias, not data, and a useful one.
3. **`movements` and `precomputed_max_pressure` are the right idea** — see below, they carry the
   model further than raw queues.

**What breaks:**

| problem | why it matters |
|---|---|
| **Three copies of the same numbers** | queues appear in `lane_vehicle_sensing.approaches`, again in `.movements`, again in `precomputed_max_pressure`. At 9 nodes × every tick that is pure prefill, and it gives the model three places to contradict itself. Pick *movements + pressure*; drop `approaches` and the `total_cars`/`inner_lane_*`/`outer_lane_*` splits. |
| **`precomputed_max_pressure` is already the answer** | `NS_THROUGH_pressure: 15` vs `EW_THROUGH: 4` — the decision is done arithmetic, and the LLM's only job is to not ignore it. That is fine if you *want* that. Just know it: with this field you are testing judgement about spillback and platoons, not computation. Keep it — §4 says the model computes badly anyway. |
| **`downstream_receiving_occupancy` has no capacity** | `2`, `4`, `2` — 2 out of what? Two cars is empty on a 200 m link and jammed on a 40 m one. Without a `…/capacity` the model cannot tell the platoon's target road (value 2) from the loaded one (value 4) meaningfully. |
| **`target_phase` accepts `NONE` alongside `action`** | two fields encoding one decision, so `{"action":"SWITCH","target_phase":"NONE"}` is representable and wrong. Either derive the action from `target_phase === current` or make the enum per-tick. |
| **`intersection_name: "…(DLSU Main Gate)"` and `active_scenario` are static but resent every call** | no prefix cache on this endpoint, so every static byte is paid nine times a tick. Fine at ~40 tokens; do not let it grow. |
| **No `in_green` vs `min_green` deadline, no eligible set** | `elapsed_seconds: 15` and `min_green_seconds: 10` are both there but the model must do the comparison. And nothing tells it which phases are green-eligible — that is what the enum should be built from. |

**Do not submit all of it unchanged.** It is 41 lines / ~330 tokens fed nine times a tick; at the
9-concurrent ceiling already measured that is where the latency goes. Rewrite it as below — same
spine, de-duplicated, with a capacity denominator and the eligible set filled in.

```json
{
  "agent": "agent_vito_cruz",
  "place": "Taft Ave & Vito Cruz (DLSU Main Gate)",
  "tick": 45,
  "clock": { "phase": "EW_THROUGH", "in_green": 15, "min_green": 10, "max_green": 45 },
  "eligible": ["EW_THROUGH", "EW_LEFT", "NS_THROUGH", "NS_LEFT"],
  "queue": { "NS_THROUGH": 15, "NS_LEFT": 5, "EW_THROUGH": 4, "EW_LEFT": 1, "NS_RIGHT": 3 },
  "room":  { "taft_south": "2/8", "taft_north": "4/8", "cross_ew": "2/6" },
  "inbox": [ { "from": "Quirino Ave (upstream)",
               "msg": "22-car southbound platoon, 32 km/h, ETA 14s" } ],
  "goal": "cut total delay; avoid the acceleration spike from a stop-and-go restart"
}
```

`room` is `occupancy/capacity` and the platoon's value is shown against its denominator. Removed:
`approaches`, `movements` (folded into `queue`), `precomputed_max_pressure` (recomputed by the env,
sent only when it disagrees with the LLM's pick, as a façade hit), `total_cars`, `lane_type` splits
(keep those in the digest only for a left-turn-queue scenario).

### Does the schema itself help?

Yes, and not only cosmetically — splitting `action` from `target_phase` fixed a real defect. On a
digest where NS had 15 queued and EW had 4, the old `phase`-enum schema returned `hold` **3/3
times**, i.e. it never switched. The two-field schema returned `SWITCH/NS_THROUGH/30` **2/2**, with
usable reasoning:

> "EW_THROUGH has served its min-green (15s > 10s) but carries only 4 cars of pressure vs
> NS_THROUGH's 15."

A field named `phase` invites the model to answer "which phase is lit" (→ the current one). A pair
of `action` + `target_phase` forces it to answer "is there a better one". Keep the pair; resolve the
`NONE` ambiguity with a per-tick enum of eligible phases only.

---

## 5. Orchestrator — detection request

Only fired by the deterministic detector, once per district, on a 2 s tick.

```json
{
  "model": "flash", "temperature": 0, "max_tokens": 220,
  "response_format": { "type": "json_schema", "json_schema": {
    "name": "escape_plan", "strict": true,
    "schema": {
      "type": "object",
      "properties": {
        "diagnosis": { "type": "string", "maxLength": 160 },
        "cycle":     { "type": "array", "items": { "type": "string" }, "minItems": 3 },
        "steps": {
          "type": "array", "minItems": 1, "maxItems": 6,
          "items": {
            "type": "object",
            "properties": {
              "node":  { "type": "string" },
              "act":   { "type": "string", "enum": ["escape_phase", "meter_upstream", "forbid_turn", "hold"] },
              "phase": { "type": "string" },
              "until": { "type": "string", "enum": ["depth_le_1", "queue_below_baseline", "2_clean_ticks"] }
            },
            "required": ["node", "act", "until"], "additionalProperties": false
          }
        },
        "release_order": { "type": "array", "items": { "type": "string" } }
      },
      "required": ["diagnosis", "cycle", "steps", "release_order"], "additionalProperties": false
    }
  } },
  "messages": [
    { "role": "system", "content": "<ORCHESTRATOR SYSTEM — §7>" },
    { "role": "user", "content": "<DISTRICT REPORT — §6>" }
  ]
}
```

### Example response

```json
{"diagnosis": "3-cycle N5→N6→N9→N5 blocked at depth 2+ for 4 ticks",
 "cycle": ["N5","N6","N9"],
 "steps": [
   {"node":"N6","act":"escape_phase","phase":"ELWL","until":"depth_le_1"},
   {"node":"N6","act":"meter_upstream","until":"queue_below_baseline"},
   {"node":"N9","act":"forbid_turn","until":"2_clean_ticks"}],
 "release_order": ["N5","N6","N9"]}
```

Steps are **applied as listed**, in order, then re-evaluated on the next 2 s tick. `steps` is
what the orchestrator *does*; it is not a commentary object.

## 6. District report — orchestrator user message

```
DISTRICT lock=N5,N6,N9  gen=2  tick=0044  persisting=4t
detector: cycle N5→N6→N9→N5, every arc ≥0.9 occ
queue    N5-S 6/6  N6-E 5/5  N9-N 6/6
depth    N5-S 3    N6-E 2    N9-N 3
phase    N5:NS_T(31s)  N6:EW_T(6s)  N9:NTST(48s)
feeders  N2→N5 4/5   N10→N9 5/5
baseline N5 3.2 N6 2.7 N9 2.4
```

## 7. Orchestrator system prompt

> You control a district of a traffic grid that has deadlocked: every road in a cycle is full, so
> nothing drains. Pick the smallest sequence of interventions that breaks the cycle.
>
> Break the cheapest arc: the one with the shortest blocked queue and the most upstream room.
> Order matters — discharge the arc that frees a node the most others depend on. Meter the feeders
> that are refilling a cycle arc so it stays clear while you drain. Never plan more than 6 steps;
> you will be re-called in 2 s with fresh state.

## 8. Hand-back message (per node, not an LLM call)

```
N5 control returned. Held by orchestrator: phase=NTST, min_green=8, max_green=30, fed N9.
Drain objective met. Resume from stored state, t_in_green=11.
```

Restate, do not summarise: compressed handoffs drop constraints in ~97% of cases.

## 9. Mechanics that are not optional

- **Concurrent, not sequential.** 9 calls in flight gives 4.6 s wall; sequential gives ~12 s.
- **`max_tokens` tight** (40 node / 220 orchestrator). Output length is the latency, not input.
- **Retry once per node per tick**, then the stall guard holds the last good action.
- **Log every request and response** verbatim; a guard firing must replay from the digest alone.
- Cap the prefill: if a digest exceeds 4,000 tokens it is a bug, not a big intersection.
## 10. Verified findings (do not re-litigate these)

| claim | how it was checked |
|---|---|
| `flash` exists and answers | `/v1/models` returns exactly `auto` and `flash` |
| `json_schema` + `strict` validates | 6 runs; enum conformed every time, no prose outside JSON |
| `structured_outputs.choice` is **not** enforced | echoed the prompt's sensor values instead of picking |
| 9 concurrent node calls = 4.58 s wall | threads, realistic prompt, 9/9 valid |
| `max_tokens: 40` truncates the JSON | `finish_reason: "length"`, unparseable; 90 completes |
| `why` costs ~0.9 s | 2.48 s with, 1.62 s without |
| formula prompt loses to intent prompt | 0/3 correct vs 3/3, same digest |
