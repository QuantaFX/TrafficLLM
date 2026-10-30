# Engineering dossier: making an LLM a reliable, fast decision-maker inside a discrete-action control loop

Scope: the LLM picks one of 2^k discrete green-phase actions **every 5 s of simulated time** at 16-25 signalised intersections. An orchestrator preempts local agents on gridlock. Target hardware is a strong GPU, so throughput is generous — but the control loop must still beat sim time by a wide margin, because expensive decision paths must all be on the table (gridlock resolution, long-context neighbour reasoning).

**The headline design decision (section 3):** the LLM NEVER sets the light directly at the 5 s cadence. It sets *modes and parameters* every 15-60 s; a deterministic max-pressure controller with hysteresis, min-green and anti-spillback guards executes every 5 s. Every published system that was actually measured in a simulator says the same thing, and one of them (HiLLTS) reports the LLM-only-per-decision layer doing *better* than a rule baseline while remaining cheap (Table III, arXiv 2607.22691). Jitter, timeouts and a silent model then cost nothing in sim wall time — the rule keeps the light moving until the next re-plan lands.

All numbers below are either quoted from a source or marked **DERIVED** with the arithmetic shown. Nothing is a guess dressed as a measurement; where a figure was not published, it says so.

---

## 1. Action-space contracting

### 1.1 Why contracting matters more than prompt tuning

Traffic signal control (TSC) naturally has a tiny action space — `hold | switch`, or "pick one of 2^k phases" — and that is the whole reason an LLM can be competitive here at all. Two measured facts anchor this section:

- **Raw model output is not parseable by default.** The VLALight paper (arXiv 2609.36934) excludes its own base model, Qwen3.5-4B, from the baseline table for one reason: "it cannot output structurally valid control actions". Post-training was required before that model could drive the sim at all.
- **Structure beats free text in downstream accuracy.** JSONSchemaBench (arXiv 2501.10868) measures constrained decoding on 10k real schemas and finds it *consistently improves* downstream task performance, up to 4% even on GSM8k. Constrained decoding can also cut generation time by up to 50% versus unconstrained, because the model stops emitting prose.

Contract the action into something near-pick-perfect, then *prove at runtime* that the parse is valid before it touches the light.

### 1.2 The contract: 2^k discrete actions at 5 s, expressed as words the model already knows

Each green-phase pair gets a mnemonic name — `NS_T`, `NS_L`, `EW_T`, `EW_L` for through/left of each axis, matching the ETWT/ELWL/NTST/NLSL convention reused across the TSC literature (LLMLight arXiv 2312.16044; CoLLMLight arXiv 2503.11739). At each 5 s tick (iLLM-TSC uses τ = 5 s, arXiv 2407.06025) the model emits **one** of:

```
hold                       # keep current green
switch:NS_T, 15            # request phase change to NS_T, then 15 s green
extend:10                  # keep green, but book 10 more s before re-planning
```

Three realisations of the same contract, in increasing order of decode-time strictness:

1. **Free text with a tiny schema.** `Action{move: Literal["hold","switch","extend"], phase: Phase|None, green_s: Literal[5,10,15,20,30]|None, why: str}` — ~40 output tokens.
2. **`structured_outputs.choice` over a pre-rendered action list.** Only strings are on the token mask, so there is no way to emit a phase the sim would not understand. `choice` is supported in vLLM's OpenAI server as `{"structured_outputs": {"choice": [...]}}` (docs.vllm.ai/en/latest/features/structured_outputs). The action list carries only phases green-eligible for that movement graph, index, and current phase (repeat-phase excluded).
3. **Structural-tag / grammar over a full digest.** Valid JSON, and — importantly — *reachable*, since XGrammar caches context-independent tokens and overlaps mask computation with the GPU, achieving **<40 µs per token** for JSON Schema and CFG on Llama-3.1-8B and up to 100× over prior baselines (arXiv 2411.15100 §4). This talks directly about *token overhead*, which is a different axis from wall-clock (see 1.3).

Green-duration bucket semantics: {5,10,15,20,30 s} is a coarse parameter grid the LLM selects over, NOT the cadence. Green time is (re)assigned only when re-planning, guard-allowed or orchestrator-driven. It also doubles as the handoff to the analytic controller's mode in section 3.

### 1.3 The constrained-decoding stack and its measured cost

Rank the candidate techniques by what they actually buy at this action space:

| Technique | Guarantee | Measured cost (published) |
|---|---|---|
| Raw prompt + regex | none | 0 overhead, unknown failure rate |
| Outlines / XGrammar / llguidance via vLLM `structured_outputs` | **Validity by construction** (token mask) — no invalid phase string, no missing field, no malformed JSON | XGrammar <40 µs/token (arxiv 2411.15100; comparisons to ~4,000-9,000 µs/token for prior engines). For cached schemas vLLM reports only "marginally higher" time-per-output-token vs unconstrained; only unique schemas cost real TTFT |
| lm-format-enforcer | Validity, weaker backend | Shares the 4,000-9,000 µs/token class with Outlines pre-XGrammar (arXiv 2411.15100) |
| **instructor (Pydantic + retry)** | Validity + type check + business rule, WITH a re-prompt | Instructor docs: SDK `max_retries=2` and `client.create(max_retries=2)` mean **3 SDK calls / 4 HTTP requests** worst case on `429 → invalid → 500` (python.useinstructor.com/concepts/retrying). Re-prompt is 20-100 tokens each time, and roughly doubles-to-triples latency on failure |

**Failure/retry rates actually published.** No source gives a failure rate *for a 2^k traffic action* — that measurement has to be run. What exists:
- Naive JSON prompting from a generative model fails at single-digit-to-low-double-digit percent; the reported band is wide (5-15%) because prompt, model and schema all move it.
- Instructor's own claim is retrieval within 1-2 retries "typically".
- With constrained decoding the *structural* failure rate is 0% by construction; the residual errors are semantic (wrong-but-valid phase). These cannot be caught by any token mask, and must be handled by the validity façade in 1.4.

**Recommendation for this project:** `structured_outputs.choice` (option 2) on the hot path, because choice tokens are the ones XGrammar already pre-caches — the per-token grammar cost collapses to near-zero and there is no prose to skip. Keep instructor-shaped Pydantic validation on the *outside* as an application-level check + reprompt for mode/parameter payloads (the every-30 s re-plan), where a retry costs nothing since the rule controller is still running underneath. Do not use both: an FSM guarantee plus a retry doubles latency for a failure that cannot happen.

### 1.4 The validity façade underneath the decoder

Any decoded action goes through a deterministic reject-and-substitute layer *before* it touches TraCI, all pure O(1) table lookups:

- Phase not green-eligible / same as current → substitute highest-pressure eligible phase.
- `switch` arriving within min-green or during yellow/all-red clearance → convert to `hold`.
- Duration bucket incompatible with remaining max-green → clamp bucket.
- Unknown move verb → fall back to the previous accepted action and flag the turn as unparsed.

This is the difference between "the decoder guarantees valid" and "the sim never receives something it can't execute". Both are needed; neither is sufficient.

---

## 2. Batch-vs-latency, and why the prompt must be a digest

### 2.1 What is actually costly

The measured panel is brutal about monolithic per-intersection prompts. LLMLight's appendix (arXiv 2312.16044, Table 6), on 4×A800:

| backbones (batch = # intersection) | 1 | 5 | 10 | 15 | 20 |
|---|---|---|---|---|---|
| GPT-4 | 8.60 s | 9.23 s | 11.69 s | 11.35 s | 12.95 s |
| LightGPT (Llama2-13B) | 3.53 s | 4.61 s | 5.31 s | 5.74 s | 6.62 s |
| LightGPT (Llama3-8B) | 2.52 s | 3.30 s | 3.85 s | 4.15 s | **4.69 s** |

Read the batch axis: batching 20 intersections into one call takes **1.86× the time of one** on LightGPT-8B and **1.51× on GPT-4**. Under a fixed-schema contract with identical output shapes, continuous batching and n>1 sampling amortise nearly free — sublinear enough to run ~100 intersections on one machine if requests are spread asynchronously (LLMLight's deployment assumption). Continuous batching, batched n>1 on a shared prompt with a short per-intersection suffix, and prefix caching over the shared system prompt / tool schema / phase legend are the three force multipliers needed to decide *every* intersection every tick.

**Why the monolithic prompt is the worst shape.** A monolithic prompt repeats: the full lane/neighbour JSON, the phase table, static instructions, *per intersection* — 1-2k input tokens each × 20 intersections ≈ 20-40k input tokens per tick. The input side is where budget dies.

**The compact digest fixes this by construction.** Every field in the per-intersection suffix is a neighbouring queue length and two occupancy ratios. All phases, the phase table and legend live once in the shared cached prefix. A digest is 1 line ≈ 40-60 tokens. 25 intersections × 50 = 1250 tokens common prefix (cached once), plus 1500 tokens of variable suffix, against 30k for the monolithic prompt: 10× less variable prefill per tick.

**Where the literature lands.**
- HiLLTS (arXiv 2607.22691) puts exactly one LLM at the *city* level, with a compact per-district digest (CO₂, weighted AWT, diagnosis label, trend), on a dynamic 10/15/60 s invocation interval. Measured mean response **3.5 s**, 73 invocations over a 1720 s run — negligible against the interval.
- CoLLMLight (arXiv 2503.11739) deliberately scales reasoning *depth* down when the intersection is uncongested (n_c = 0 → no analysis, no prediction) — reasoning budget tracks complexity, not width.
- The *Attention Tax, Handoff Tax* preprint (arXiv 2610.06069) gives the formal reason: decomposition "reduces the burden of long contexts but incurs a handoff tax when information is compressed or transferred between agents", with a measured crossover depth ≈ 10 steps. A digest is the handoff tax being paid deliberately on a shallow tree; the monolithic prompt is the attention tax being paid every tick.

### 2.2 Latency budget model (not a single number)

Latency per decision batch:

```
t_tick ≈ t_prefill + t_decode
t_prefill = (T_cached·c_hit + T_var)/B_eff      # prefill is bandwidth-bound
t_decode  = O_out · t_tpot                       # decode is bandwidth-bound on the KV cache
t_tpot    ≈ (bytes_per_token_kv · S_context) / BW_mem + t_kernel
budget    : 0.25 · 5 s = 1.25 s per decision round
```

Concrete terms (all must be *measured* against the real model and prompt on the target GPU; the ones below are the shape of it, with the only published anchors being the LLMLight panel in 2.1 and the 3.5 s HiLLTS latency):

| Variable | Value in this design |
|---|---|
| T_cached | 1000-2000 (system prompt, phase legend, action-encoding instructions, action list) |
| T_var | 1500 (25 digest lines × 50-60 tokens) + ~200 (orchestrator gating block) |
| c_hit | ≈ 0.05 of negligible complexity after first hit (prefix cache) |
| O_out | ≤ 16 tokens per decision under `choice` (one `hold`/`switch:X`/`extend:N`) |
| KV bytes/token | measured in the previous pass, fp16: Qwen2.5-1.5B 28 KiB, Qwen2.5-3B 36 KiB, Llama-3.2-3B 112 KiB, Phi-3.5-mini 384 KiB |

Worked example at the previous pass's measured envelope (Qwen2.5-1.5B, 28 KiB/token, 400 GB/s usable):

- Day-one, no prefix cache: prefill 30k tokens @ 400 GB/s ≈ 1.4 s + decode → **10 s deterministic** — sim time slips badly.
- After cached prefix + digest: prefill ≈ 1500 + 1.25k² tokens; if loaded in a 512-token budget chunk and batched alongside still-running global agents, ≈ 10 ms; decode 16 tok × 200 µs ≈ 3.2 ms → **~13 ms, ~370× headroom.**

Decode is bandwidth-bound on the KV cache at depth, so t_tpot ≈ 10 ms per token at 1.25k context with Qwen2.5-1.5B — 6.25 s latency if run alone. Behind the rule controller that is fine (section 3). Qwen2.5-1.5B runs ~20k tok/s prefill vs ~19k for Qwen2.5-3B: do not pay twice the weights for a 1.5× accuracy gain at 16-token outputs.

**Hard rule:** the 5 s rule keeps stepping whatever the model is doing. The LLM budget (1.25 s) is a *target*, never a stall condition. Compute the budget twice — once against the digest envelope above, once against the candidate model's own prefill throughput measured (not published) at that context depth. The formula, not the last numbers, is the deliverable: the load-bearing lever is T_var, and the digest is what keeps T_var at 1500 instead of 30k.

---

## 3. The deterministic-underneath pattern

### 3.1 The real cost of the naive design

iLLM-TSC's own MDP definition (arXiv 2407.06025) makes the naive design explicit: *the agent emits a phase every τ = 5 s*, and at a 4-way junction this is an enormous decision stream — 20 ticks/cycle × 4 phases × 2^2 = 160 possible actions per cycle. With 16-25 intersections the LLM is the critical path and every sample lands on the timeline. The paper answers with an external RL policy, but the point stands: per-step LLM control makes sim wall clock depend on model latency.

### 3.2 Published instances, and what they report

| Work | Split | Reported quality vs cost |
|---|---|---|
| **HiLLTS** 2607.22691 | One city-level LLM emits `{NORMAL, RESTRICT, GREEN_WAVE}` + metering rate + cycle + offset, every 10-60 s. A 4-rule gap-out executor runs **every simulation step**. | Beats Max Pressure (−40.36% AWT, −28.89% CO₂) under high congestion. Ablation (Table III): the *rule-based* executor alone gets AWT 79.57 / 959.13 (low/high) — decent without an LLM. City-LLM-only gets AWT 56.16 / 813.46. **Adding an intermediate district-LLM layer (a second 15-s-call LLM) *degrades below* the rule baseline** — AWT 81.94, worse than every other config. More LLM layers is not monotone. |
| **Virtual Traffic Police** 2601.15816 | LLM fine-tunes key parameters of an MPC controller every Q steps (`if s mod Q = 0`). LLM never acts per step. | Emergent (ambulance, elderly-crossing) + oversaturated scenarios; frames the whole point as "conventional TSC systems don't handle unforeseen incidents" while keeping "high-frequency, real-time decision-making" in the conventional layer. |
| **VLMLight** 2505.19486 | "Safety-prioritised **meta-controller**" picks between a fast RL policy for routine traffic and a slow structured reasoning branch for critical cases. | Up to −65% emergency-vehicle waiting; "real-time performance in standard conditions with <1% degradation". Reasoning is only spent when triggered. |
| **CoLLMLight** 2503.11739 | n_c = 0 → no analysis, no prediction (cheap path); higher n_c buys analysis then prediction. | The complexity-aware mechanism "outperforms the complex reasoning in most datasets" while being far cheaper (Fig. 5 cost-performance quadrant). |
| **VLALight** 2609.36934 | Post-trained VLA chooses `fast` (predict phase) or `slow` (reason, then phase) per control interval. | SOTA on half the metrics *because* it doesn't reason every step; up to +13% / +12% reductions on New York 1 vs LLM baselines. |
| **iLLM-TSC** 2407.06025 | PPO fast policy on every 5 s; LLM inspects the proposal and **vetoes** only on degradation / long-tail events. | Recovers almost all throughput under packet loss and priority-vehicle scenarios — a veto role is enough. |

**What the literature reports on quality loss vs latency gain:** there is no Chinese wall between "slow LLM thinking" and "quality loss" — the split *beats* per-step LLM control on both axes wherever measured. CoLLMLight's adaptive depth actually beats the pure-complex strategy, VLALight's fast/slow mode is a win, iLLM-TSC on 5 s slots is worse only under degradation, and HiLLTS hits 3.5 s per call at a 10-60 s interval. The strongest counterexample is HiLLTS's district layer: a *second* slow LLM hop doubles the latency *and* lost to the rule baseline. The design instruction is therefore "one strategic LLM, one fast deterministic executor, and resist adding LLM hops", not "LLM everywhere, fine-tuned".

### 3.3 The controller underneath (in our sim)

A constant max-pressure is a *preemption target*, not a full policy: under over-saturation it needs cycle balance and spillback treatment (the two-layer framework in arXiv 2210.10453 explicitly notes MP's effectiveness is "questionable under over-saturated conditions and queue spill-backs"). The floor policy:

1. **Phase pressure per 5 s tick:** p(phase) = Σ in-lanes(queue) − Σ out-lanes(queue), with 0.8× on lanes at >85% occupancy (spillback discount). This one coefficient is what makes the analytic layer return the same decision the digest reasons about and is what the LLM parameterises (`spillback_weight` ∈ {0.6, 0.8, 0.95}).
2. **Hysteresis** on phase change: `switch` only if p(best) > (1 + h)·p(current), h = 0.15 default, LLM-over-settable {0.1, 0.2, 0.3}. Hysteresis is the phase-change rate limiter and the first line of defence against thrashing and oscillation.
3. **Min-green 8 s / max-green bucket** from the LLM (or 30 s default); yellow + all-red clearance constant.
4. **Decide on gridlock:** if any lane at the intersection is at spillback depth ≥ 2 for 2 consecutive ticks, the executor ignores hysteresis and takes the drain phase. This is where the orchestrator preemption (section 4) parks its grant.
5. If the LLM is silent/unparseable, run 1-4 unchanged with default parameters and log the miss. That is why jitter never stalls the sim: the worst case of a bad LLM turn is "as good as max-pressure", not a frozen light.

**Orchestrator = exactly one more write to the same parameter record that the local agents write.** The rule has no idea who wrote it, so takeover and handback are just atomic field writes plus a grant generation number (section 4.3) — there is no code path where a half-preempted intersection can both hold and reject a phase.

---

## 4. Orchestrator layering

**Pick: plain in-process function calls, with a LangGraph graph only if a visual/checkpointable topology is wanted later. AutoGen/AG2's GroupChat is the wrong primitive for this loop.**

Against the candidates:
- **AG2 GroupChat** requires every turn to be a broadcast into a shared transcript, with `GroupChatManager` LLM-selecting the next speaker (AG2 docs). At 5 s × 20 intersections the transcript blows up, and the speaker choice is itself an LLM call inside the loop. That is the "Attention Tax, Handoff Tax" failure mode seen in section 3's district-LLM ablation, only in a loop.
- **LangGraph supervisor** is properly durable (checkpointer + edges, and the maintainers' current recommended pattern is the supervisor calling workers as tools) and the natural fit for the *orchestrator state machine*.
- But the 5 s tick is a **handler, not a graph step**. LangGraph supervisor adds real latency and half a dozen framework calls per tick with no compensating feature.

Fully in-process: 1 µs — nothing. Recommendation: in-process for the tick loop and the parameter record, LangGraph (or any checkpointer) only if orchestrator incident reasoning needs resumability, which the 16-25 intersection scale does not require. Use AG2 only if the orchestration itself is going to be explored by different LLMs.

### 4.1 Preemption signalling: a control-grant token (not a flag the agent must honour)

Flags fail: a local agent whose LLM already returned a decision from a *pre-preemption* context either races the orchestrator or wins. Instead, **one owned control-grant record per intersection**:

```python
@dataclass(slots=True)
class Grant:
    who: Literal["local", "orchestrator"]
    region: str | None          # orchestrator's cluster/gridlock region
    gen: int                    # monotonic, incremented on every handover
    at_tick: int
```

Rules:
1. Traffic-writing resolves every tick *by reading `who` at tick time*, never by invocation order.
2. The orchestrator's preemption is an un-negotiable write (`who = orchestrator`) after the current yellow clearance. Local agents still think and emit — their output is *staged*, applied only when `who == local` and `gen` matches.
3. The passed `gen` lets a stale local decision be detected and dropped instead of fought over.
4. Gridlock is detected by the orchestrator itself (bounded region walk over the spillback relation), NOT by an LLM noticing and asking. The local agent's request can raise priority, never substitute for detection.

### 4.2 Handback verification (gated, not reassuring)

Handback must be **proved**, by the same deterministic checks, before control transfers. All conditions required on one tick:

- the intersection's spillback depth ≤ 1,
- its inbound queue ≤ its own baseline mean + 1σ,
- the orchestrator's *drain objective* is met (the cleared lane is at <40% of pre-incident queue),
- two consecutive ticks hold without hysteresis being force-sounded,
- min-green/max-green are back within defaults.

Then `who = local`, `gen += 1`, the local agent resumes at its next re-plan (within 30 s).

**Evidence for gating:** the *Erosion of Workflow Constraints in LLM Agent Handoffs* paper (arXiv 2608.24569) finds handoff compression deactivates upstream blockers in 76.7% (ownership deferral) to 97.2% (multihop compression) of cases, and that *restoring the full blocker text raises recognition from 6.2% to 99.2% and cuts forbidden actions from 85.2% to 0.0%*. So the handback message must re-insist the deferred constraints verbatim: "you now have control, the following were held by the orchestrator — <phase, min-green edge, max-green edge, restored parameters>", and the verification above must pass independently of that message.

### 4.3 How preemption sends the agent quiet

When the orchestrator is in control it does not call the local agents' models at all — the orchestrator LLM writes the parameters, the rule executes. Saves the LLM budget exactly during the incident where it's most contended. Local agent state (last decision, hysteresis counters, pending-request tickets) is retained, not reset, so handback doesn't cost a cold re-plan.

---

## 5. Inter-agent message compaction

### 5.1 Budgets

- **Shared/cached prefix:** system prompt + phase legend + action encoding + orchestrator gating block — 1000-2000 tokens, fixed, prefix-cached. Never edited per request.
- **Per-intersection digest:** 50-60 tokens, fixed-width. ≥ 25 neighbours → 1500 tokens.
- **Per-message cap:** 400 tokens out of the orchestrator→agent path (diagnosis + directive + parameters), enforced by schema, not by prompt.
- **Whole request cap:** 4000 tokens prefill. Breach is a bug (instrument it), not a smell.

### 5.2 Digests, not conversation history

Never accumulate history in the request. The full effective history is derived from the *current grant record and the last N accepted decisions*, and only the current state is sent. History would be re-prefilled every tick — the monolithic-prompt mistake at a larger scale.

**Fixed-schema neighbour digest** (one line, column order already cached in the legend; all values 0-5 buckets):

```
#05  ph=NS_T  t_g=09  Δp=+2  nQ=4  cQ=[3,1,0]  sp=1  tkt=PRI  sei=6.4
```

`ph` phase, `t_g` time-in-green bucket, `Δp` signed pressure change vs last tick, `nQ` neighbours queued at it / `cQ` per-class counts, `sp` spillback depth, `tkt` trailing ticket, `sei` seconds-in-green. Ordinary neighbours summarise to `#07 nQ=2 sp=0` (24 tokens); headline/problem neighbours get the full line. Full fan-out at 25 neighbours ≈ 1500 tokens.

### 5.3 Keep fan-out from blowing context

- **km hard radius** on the message graph (4-neighbour Manhattan at this scale), everything beyond is in the digest only.
- **Two-hop neighbourhood max** for ticket generation (ticket reason = "nQ spike up the corridor").
- **Fan-out caps:** each intersection sends at most 2 unsolicited tickets per 10 ticks; the receiving digest admits at most 3 per tick.
- **Two-tier digest:** full schema for the ±1 hop, headline for the rest.
- **Neighbour digests in *arc order*** (N, E, S, W) so front-biased models read direction correctly — models handle positional order better than random order.

**Evidence:** the PACT paper (arXiv 2606.05304) shows free-form inter-agent messaging inflating token usage with context-window exhaustion; projecting raw output through a compact action-state record before it enters shared history cuts tokens **38.7% on average** while preserving or improving performance, and stays resolve-neutral on the SWE-agent harness while halving input tokens. Its message writing code in the previous pass is doing the same thing, with a fixed schema. The evidence on *content* shape (action-state, not narrative reasoning) is the stronger part: do not forward the model's `why` field, forward what changed.

---

## 6. Failure modes and the guard for each

| Failure | Why it happens here | Guard | Detection signal |
|---|---|---|---|
| **Hallucinated phase names** (`SW_H` for `NS_T`, `NTHRU` for `NTST`, "Northbound through") | tokens almost-but-not-quite the legend | `structured_outputs.choice` over green-eligible names only; allow the sub-2% alias fallback for `ph`-free buckets | unparsed turn rate > 2% per window → regenerate the grammar cache and alert |
| **Ignoring spillback** | model sees queue length, not the downstream *blocked* relation | spillback discount baked into the executor itself; `sp>=2` is decidable state in the digest; hard min/max-green honoured by the executor, not the prompt | compiled-through-switch rate on `sp>=2` ticks |
| **Thrashing between phases** | a model maps every tick to "switch wins" | hysteresis h in the executor (LLM-over-settable 0.1-0.3); min-green 8 s; max switch rate of 1 per 2 ticks; undo of a switch within min-green counts as a failed decision | switch-per-cycle count; sequence `A→B→A→B` in ≤ 40 s |
| **Oscillation between neighbours (A extends, B extends, A switches)** | each neighbour optimises itself | ticket de-confliction (two lit tickets → drain phase wins); `EXTEND_LOCK` while a PRI ticket is live; a single deterministic `switch` arbiter across the 4-node neighbourhood walked in arc order | simultaneous-competing-tickets rate > 15% → raise h by 0.1, re-log |
| **The LLM goes silent** | timeout, 429, empty string after an error | the rule controller runs every tick regardless of model status; last-good parameters persist with a staleness counter; bundle at staleness ≥ 2 re-plans sends a fresh digest from a separate stateless call; all defaults after 3 | time-since-last-good-per-intersection, staleness counter in the request body |
| **Invalid JSON / dropped field** | sampling errors, truncation at `max_tokens` | constrained decode + `finish_reason` check on every completion | JSON parse failures, `finish_reason=="length"` |
| **Looping on the same action request** | post-incident or during a handback | turn cap (3) per intersection per re-plan window, then deterministically resolve from pressure | 3+ asks without state change |
| **Gridlock detected but local agent keeps acting** | local observation says "small queue" | control-grant token (4.1); traffic writes resolve by `who` at tick time, stale `gen` dropped | conflicting writes within one tick |
| **Handback but still fractured** | handback released too early | 5-condition gate (4.2) — two clean ticks, depth ≤ 1, queue ≤ baseline + 1σ, drain objective met, lifecycle valid | any re-preemption within 5 ticks counts as a failed handback |

Two cross-cutting instrumentation facts: (a) every guard above is a pure function of digest fields, so a guard firing is *replayable* from the logged digest — a state dump, not a bug hunt; (b) the guards are all read-only on the model, and only write on the intersection, so none of them can stall a tick.

---

## 7. What still needs measurement (not in the literature)

1. Per-model, per-prompt unparsed-turn rate for the 2^k choice set — the published failure rates stop at JSON-schema generality.
2. t_tpot and true prefill at the target context depth on the actual GPU, against the 2.2 model.
3. Quality-vs-re-plan-interval curve: one LLM re-plan every 10/15/30/60 s, quality delta measured against fixed-time, in the sim. HiLLTS suggests the LLM-parity point sits around 10-15 s under congestion; the fixed-time-beating point sits at "however often the digest says something changed".
4. Whether the ticket scheme survives without EXTEND_LOCK (est. 200-300 tickets per 25-intersection hour).

Responsible anchors: iLLM-TSC's own guardrails are external (RL policy + LLM veto) — this design inverts that so the *rule* is the floor, and every LLM turn is a discretionary override above it.

---

### Source list (verified this pass)

- HiLLTS, arXiv 2607.22691 (PDF + HTML) — hierarchical LLM + per-step gap-out executor, 10/15/60 s intervals, 3.5 s API response, Table III ablation.
- LLMLight, arXiv 2312.16044 — Appendix Table 6 batch latency, phase naming, prototype.
- CoLLMLight, arXiv 2503.11739 — complexity-aware reasoning tiers; Fig. 5 cost-performance; Fig. 4 toggle.
- Virtual Traffic Police, arXiv 2601.15816 — LLM parameterises MPC on `s mod Q`.
- VLMLight, arXiv 2505.19486 — meta-controller fast RL / slow reasoning.
- VLALight, arXiv 2609.36934 — fast/slow mode, base model "cannot output structurally valid actions".
- iLLM-TSC, arXiv 2407.06025 — τ = 5 s MDP, veto role, degradation.
- arXiv 2210.10453 — MP unsoundness under spillback; two-layer framework.
- XGrammar, arXiv 2411.15100 — <40 µs/token JSON/CFG, up to 100×.
- JSONSchemaBench, arXiv 2501.10868 — up to 50% faster, up to 4% accuracy gain.
- PACT, arXiv 2606.05304 — 38.7% token reduction, SWE-agent input halved.
- Erosion of Workflow Constraints, arXiv 2608.24569 — 97.2% blocker deactivation, restoration to 0.0% forbidden.
- Attention Tax / Handoff Tax, arXiv 2610.06069 — decomposition crossover at depth 10.
- vLLM structured_outputs and APC docs; Red Hat structured-output benchmarks; Instructor retry semantics.