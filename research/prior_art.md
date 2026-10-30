# Prior-art dossier — LLM-agent traffic signal control (with the node-semantics rule)

**Status: citation-grade survey.** Resetting note: the task's `IDEA.md` was for the *adjacent* (road-arc) design. This dossier is written against the **node-semantics** design:

- Per node: Each signalised **node** carries the agent. Each node's agent only observes the **arcs** that touch it, i.e. the **road arcs** that either enter (upstream) or leave (downstream) the node.
- Each node's agent sees "Pressure" (pressure per node) as: `p(a) = max(0, up(a) − down(a))` on movement `a` (upstream queue minus downstream queue, floored at 0 against the conceptual pressure), maximised over movements in a candidate phase. Pressure is used to choose a phase and decide **hold vs switch**.
- Node agents **communicate** with neighbour node agents (the arcs that connect them).
- An **Orchestrator LLM** detects gridlock and **overrides** the subagents; it keeps control of the **affected nodes** until resolution, then **hands control back**.

All arXiv counts below are machine-checked against the arXiv API on **2026-10-29** (queries in `research/_arxiv_raw.json`). Abstracts for all IDs below are pulled from the API and stored in `research/_abs.json`; full texts read live in `research/_pdf/` and `research/_fulltext/`.

---

## 0. Landscape counts (measured, not guessed)

| Query (arXiv `all:`) | Hits | Reading |
|---|---|---|
| `"traffic signal control" AND "large language model"` | **26** | small but real; ~20 directly on-topic (the rest are RL/adjacent) |
| `"traffic signal" AND "LLM agent"` | 5 | tight core |
| `"traffic signal control" AND "multi-agent" AND "large language model"` | 7 | core |
| `"max pressure" AND "large language model"` | **0** | nobody has bridged these two vocabularies |
| `"traffic signal" AND deadlock` | **2** | gridlock is nearly absent from this literature; neither hit is an LLM paper |
| `"gridlock" AND "traffic signal"` | **2** (both: OverFlowLight 2606.27381, auction metering 2508.09678) | — |
| `"traffic signal control" AND orchestrator` | **2** | neither is the described architecture |

The absolute literature is small. That cuts both ways: there is room, but the *shape* of the design is heavily pre-figured — see §1.

---

## 1. LLM / multi-agent-LLM TSC papers, newest first

Each entry: mechanism in 3–5 lines, **LLM control** (cadence / model / latency handling), and **distance to the design**.

### 1.1 Evocation of the Orchestrator pattern: scored

| # | Paper | Per-node agent? | Agents talk? | Supervisor preempts locals? | **Verdict** |
|---|---|---|---|---|---|
| 1 | HiLLTS (2607.22691) | **No** — lower layer is a *rule* controller | No | City LLM issues directives that "override the base policy" | **~65% of the design.** Missing: per-node LLM, node–node talk, gridlock-specific trigger, hand-back |
| 2 | CoLLMLight (2503.11739) | **Yes** (one agent per intersection) | Only implicitly (neighbour occupancy baked into the prompt) | No | ~50 %. Its own conclusion names a "regional-level planning" tier as future work |
| 3 | Virtual Traffic Police (2601.15816) | No — single intersection | No | Upper LLM **is** the preemption layer; tunes MP weights | ~45 %, but the *preempt-then-restore* framing is explicitly human-police |
| 4 | TrafficGen (Frontiers AI 2026) | "IntersectionAgent(s)" | Via a Supervisor *router* | Supervisor routes to emergency/intersection agents | ~55 % architecturally, but **single-intersection text twin**, deterministic, no gridlock |
| 5 | Traffic-R1 (ACL 2026) | Yes | **Yes — explicit async message passing** | No | ~45 %; the comms layer is the closest match, the supervisor is absent |
| – | *Design under review* | Yes | Yes | Yes, gridlock-triggered, with hand-back | — |

*(This table is the whole competitive picture: no published work closes all four columns.)*

*(Scores are my own reading of the abstracts/full texts, not the authors' claims.)*

**Be brutal:** the orchestrator pattern is *not* novel in the abstract — two-tier human/manager-override is the classic traffic-management structure, and three 2026 papers (HiLLTS, Virtual Traffic Police, TrafficGen) each ship a version of it. What nobody has is the *specific* conjunction: LLM per node **and** node-to-node a2a messages **and** a supervisor that keys on **gridlock** and **hands control back**. That conjunction is the only defensible claim, and §5 says how fragile it is.

### 1.2 Papers — newest first

**2609.22746 — ProcessLight: Process Supervision for LLM-Based TSC.** EMNLP 2026.
- Mechanism: decomposes each signal decision into verifiable **semantic reasoning steps**; trains with step-wise credit assignment (STeP-PO) over a step tree, scoring each step's quality and influence on the final action.
- LLM control: one LLM call per phase decision; model is a fine-tuned backbone (not specified in abstract); no latency handling.
- Distance: single-intersection, per-node agent, no comms, no supervisor. Competes on *reasoning quality*, so a shiny LLM-per-node pipeline must beat it on a metric that isn't only AWT.

**2608.18263 — SIGMA.** SUMO, multi-faced RL + LLM translating emergency instructions into priority vectors; four Kolkata intersections. Explores **graceful degradation under LLM failure** — useful to copy. No comms, no supervisor.

**2604.25259 — DGLight (thesis).** DQN (CoLight-style critic) scores LLM candidate actions; GRPO fine-tune; Jinan/Hangzhou. LLM call per decision; no latency handling. Pure single-intersection.

**2604.23902 — LLM-Augmented TSC with LSTM prediction.** LLM **evaluates candidate actions** from an LSTM predictive controller; a **safety filter** validates every output — "LLMs as constrained reasoning and decision-support rather than direct low-level controllers" is its stated thesis. SUMO; balanced/peak/surge demand.

**2604.05663 — CuraLight.** IJCNN 2026. RL agent collects trajectories → imitation fine-tune of an LLM; multi-LLM **debate ensemble** scores candidates for preference supervision. Training-time ensemble, inference-time single model.

**2604.05535 — SignalClaw.** Evolved, interpreter-checkable skill code; an event detector + priority dispatcher composes skills at runtime for EV/incident. Reported delay 7.8–9.2 s; EV delay 11.2–18.5 s vs 42–95 s for MP/DQN.

**2604.17456 — TrafficClaw.** Single LLM agent for multiple urban subsystems (signals, freeway, transit, taxi) with persistent memory and agentic RL. **Not** per-node — the agent is the controller.

**2601.15816 — Virtual Traffic Police.** NUS, Elsevier preprint. Upper LLM tunes **max-pressure saturation weights** and MPC capacity parameters in response to incidents; RAG over a "traffic language database" + LLM verifier that self-refines the retrieval store.
- LLM control: invoked every **Q** steps (Q is a free parameter; the paper's algorithm gated on `s mod Q = 0`); DeepSeek-V3-671B, GPT-4o; temperature 1 for the generator, 0 for the verifier. **No latency measurement at all.**
- Latency stance: argues LLM inference "may hinder real-time responsiveness" and that phase transitions "demand sub-second-level responsiveness" — so the LLM sits **above** a conventional controller that does the sub-second work.
- Distance: single isolated intersection; MP is the lower layer; the orchestrator only tweaks parameters and never takes over phase selection. Its conclusion asks for "large-scale urban networks" — exactly your setting.

**2511.19253 — MAESTRO.** Moves the LLM *out* of the loop entirely: offline curriculum + reward-function generation for MADDPG. 16 Hangzhou intersections.

**2511.00136 — HeraldLight.** Dual LLM: Herald forecaster, LLM-Agent, LLM-Critic that repairs hallucinated actions. CityFlow, 224 intersections; "parallel fine-grained" per-intersection agents. No comms, no supervisor.

**2510.26242 — REG-TSC.** Reviewer-RAG + emergency-aware deep reasoning; distributed per-intersection agents, ~**one LLM call per intersection per simulation step**; Llama-3.1-8B with LoRA; SUMO, 17–177 heterogeneous intersections. Deep reasoning only activates for emergency nodes (a cheap complexity gate). No comms, no supervisor.

**2508.02344 — Traffic-R1.** ACL 2026 (aclanthology 2026.acl-long.995), HKUST-GZ + PCITECH. Qwen2.5-3B, two-stage RL. **Explicit asynchronous message-passing network** between neighbouring intersection agents (claims synchronized parallel workflows are either inefficient or incompatible). Deployed on a **single T4-class (Tesla T40)** for inference; 15 s green / 3 s amber / 2 s all-red in CityFlow; deployed on 55k drivers/day with ≥5 % queue reduction.
- This is the closest thing to your agent-to-agent layer. The gap: comms carry *messages* but there is no supervisory tier, no gridlock trigger, no hand-back.

**2503.11739 — CoLLMLight.** HKUST-GZ. Per-intersection agents; a **spatiotemporal graph of neighbouring intersections** (incl. occupied neighbour lanes, occupancy > α=0.5) rides in the prompt; complexity-aware reasoning picks no-coop/simple/complex modes by counting congested neighbour lanes (n_c). Fine-tuned on Llama-3.1-8B.
- LLM control: **one LLM call per intersection per decision step**; decision step = 30 s green + 3 s amber + 2 s all-red in CityFlow. Total inference-time axis is a headline axis in the ablation (2.5k–10k s scale). No concurrency or staleness handling.
- Should be the primary baseline and threat. Its related work explicitly dismisses "iterative inter-agent communication" as too slow for real time — the closest published argument to your a2a claim.

**2407.06025 — iLLM-TSC.** LLM **audits** RL actions and overrides implausible ones; −17.5 % AWT under degraded comms. The override pattern at single-intersection scale.

**2406.13693 — From Single Agent to Multi-Agent.** Small step-up study; fine-tuned LLM gains are "small".

**2403.08337 — LLM-Assisted Light.** LLM at the centre with perception/decision tools; −20.4 % AWT under sensor outage.

**2312.16044 — LLMLight + LightGPT.** The origin: prompt-as-state, LLM as controller, distilled LightGPT. 10 datasets, 15 human raters, claims beat 10 frontier LLMs. **Read the prompt design** — senior traffic engineers judge its outputs as the reference for explainability.

**2401.00211 — Open-TI.** Assistant-to-agent traffic. Operator loop: conversational LLM shells out to simulator training, with a separate agentChatAgent loop — plus some sort of training signal end-to-end.

### 1.3 Not LLM, but the right non-LLM coordination baselines

- **MA2C** (arXiv 1903.04527): localised MARL where each agent's state = own + neighbour fingerprint. The canonical "local agents, local observation" RL baseline.
- **FRAP** (AAAI 2019) and **CosLight** (KDD 2024): phase-level interaction networks and *learned* collaborator selection — the strongest non-LLM evidence for the "agents should talk" premise.
- **UrbanLLM (2406.12360):** general hierarchical manager → specialized expert agent pattern, high-level.

---

## 2. Max-pressure theory

### 2.1 Varaiya 2013 — the stability result

Varaiya, P. (2013). *Max pressure control of a network of signalized intersections*. **Transportation Research Part C 36, 177–195**. DOI **10.1016/j.trc.2013.08.014**.

Model: store-and-forward point queues, infinite storage, **fixed-known turn ratios** r(l,m), saturation flows c(l,m). A phase is a single movement (l,m) from input link l to output link m. **Definition (21) of the paper**: the *weight* of a phase is

```
w(l,m)(X) = x(l,m) − Σ_{p∈Out(m)} r(m,p)·x(m,p)
```

i.e. the upstream queue on the phase minus the average downstream queue on m. The **pressure** of a control matrix S is the saturation flow times the phase weight:

```
c(S)(X) = Σ_{(l,m): S(l,m)=1} c(l,m)·w(l,m)(X)
```

and **MP control is the arg-max over control matrices** (eq. 22–23). The paper states (p. 186) verbatim: "The pressure of a signal control matrix S is the sum of the saturation flows multiplied by the weights of all the phases that S actuates simultaneously." **Equivalently** (Levin 2023): `w_ij = x_ij − Σ_k x_jk p̄_jk`, `s* ∈ argmax Σ w_ij Q_ij s_ij`.

**Theorem 2:** MP is stabilizing whenever the mean demand lies in the interior D° of the feasible demand region; likewise Theorem 4 for adaptive MP with converging parameter estimates; Theorem 5 for weighted queues.

**Caveats Varaiya himself records:**

- **Requires the mean turn ratios and saturation flows.** (Theorem 2 assumes known; "MP does require knowledge of turn ratios and saturation flows".) Practically this is a fixed turning-movement table — a bad fit for a node agent that only sees raw link counts unless you estimate on-line (→ AMP, Theorem 4, needs convergence).
- **The projected variant `u_sat`** (forbid movements into a saturated link) is **not stable** — gridlock of a different kind (Varaiya's Example 3: all movements blocked).
- **Min-green / weighted-queue variants exist (Thm 5)** to prevent starvation; Lämmer–Treiber and the Transit-MP / mTransit-MP line (2511.00309) document starvation empirically from the CV-sensing side.
- **Saturation-flow misses ("the saturation rate of the lane drops to 0")** are exactly what the LLM in Virtual Traffic Police rewrites by hand.

### 2.2 How close is the derivative literature?

- **PressLight** (Wei et al., KDD 2019, DOI 10.1145/3292500.3330949): reward = pressure, state = lane counts. Single-agent per intersection, no comms. Its abstract claims a theoretical ε-optimality argument but the implemented controller is a one-hop RL policy — the stability theorem does **not** carry over.
- **CoLight** (Wei et al., CIKM 2019, DOI 10.1145/3357384.3357972): graph-attention over neighbours — city, queued neighbour embeddings; reward = queue, not a stability guarantee.
- **MPLight** (Chen et al., AAAI 2020): pressure reward + FRAP-style attention; "all summed queue lengths ≤ some explicit constraint"; again heuristic.
- **Efficient-MP / 2112.02336** and **Advanced-MP / 2112.10107:** restate exact pressure; the Advanced version adds running-vehicle count to the downstream side. If your "pressure" is summed with a `max(0, ·)` you are in Efficient/Projected territory.
- **C-MP (2407.01421):** Weighted downstream platoon emphasis, provable queue stability; the closest "decentralised yet coordinated" MP (stability maintained via Lyapunov analysis).
- **Levin 2023 summary (J. Transp. Eng. A 149(4), 03123001):** best single methodological reference, with the list of where MP theory's assumptions break under finite storage and work-conservation.
- **Decision timescale (2610.32280):** proves a load-dependent critical decision interval — essential reading because the LLM's per-call cadence is a decision interval, and this gives you the theory for *why* the orchestrator can't run at every step.

---

## 3. Gridlock / deadlock — the weakest-covered and most differentiating part of the design

### 3.1 What actually causes gridlock

Gridlock is a **cycle of blocked links**: every arc in a cycle is occupied to capacity, so no node in the cycle can move vehicles, so no node in the cycle empties. In a mesoscopic queue model it needs three ingredients:

1. **Finite storage** (link capacity L(m)). Point-queue models without storage can never gridlock — this is exactly why the MP theorems are blind to it (Varaiya 2013, §4.5: "the model does not permit saturation … This is a limitation" and Fig. 6's conjecture that a network with a loop "can always be subject to gridlock no matter how large the links are").
2. **A demand/route topology that closes loops** — a one-way ring, a rotary, or a ramp/gore with a U-turn path.
3. **A work-conserving green** that keeps feeding a full downstream — this is *the node agent's own choice*. The MP `u_sat` variant fails for exactly this reason: a strict "skip movements into a saturated link" rule stalls the whole cycle.

A point-queue subtlety worth citing verbatim in any write-up: Varaiya's Fig. 6 shows **the identical junction on the left gridlocks, the identical one on the right doesn't** — topology alone decides whether a given blocked-queue policy gridlocks.

### 3.2 The sparse gridlock-control literature

- **OverFlowLight (2606.27381)** is the only gridlock paper in the LLM-adjacent space. Detector/segment widgets flag queue overflow onto upstream segments, and the controller inserts a **dedicated "overflow phase"** to flush the blocking movement; a rule layer hard-masks unsafe actions before the RL/Q-network output (`a* = argmax_{a∈A_safe} Q`). Real deployment on 43 intersections, −60.4 % overflow events, +18.2 % throughput, 106 ms end-to-end. **It is *intra*-intersection and rule-based — no LLM, no multi-intersection takeover, no hand-back.**
- **Lämmer, Treiber & Rausch 2013** — inflow-regulating green that never feeds more than a link can hold. The direct intellectual ancestor of the user's "Orchestrator restriction". Checkout-free self-healing variants include the *self-healing road networks* work of these authors. Adjacent: **Tsitsokas & Kouvelas 2022/23** (MFD perimeter control + MP two-layer). **Perimeter control** (via MFD) and **auction metering** (2508.09678) are the macro cousins; neither is agent-based nor LLM.
- **Gate/metering with forbidden movements:** the standard real-world remedy is forbidding the closing turns and holding the gate approach. All formulations below assume a phase *insert*. Neither is a per-node LLM.
- **Regional v. dynamic containment:** none of the above does **hand-back with a state guarantee**. The closest is OverFlowLight's "remove the overflow phase when the queue clears".

### 3.3 Mechanism note — what the implementation must actually do
In a meso simulator, gridlock is *never detected* by a generic engine signal — the sim just freezes and demand piles up behind entry points, and average trip time rises while average speed → 0. So *any* implementation must add: (a) a per-arc full/spillback event, (b) a cycle-adjusted reachability check over the occupied-subgraph on the orchestrator restart, and (c) **an escape hatch** (or a "shrink the cordon by one ring and retry" loop) so one unresolvable movement pair can't leave you in a live-lock loop; (d) a route-follow rule so vehicles don't reverse at dead ends. Budget a day; nobody has published this for an LLM orchestrator and it is where the demo will break first.

**Honesty note:** arXiv "deadlock + traffic signal" = **2** hits, and neither is about gridlock avoidance. Gridlock in the LLM-TSC ecosystem is essentially unclaimed. That is the single strongest part of the design — and also the part where you must import outside citations (Varaiya §4.5, Lämmer/OverFlowLight) rather than pretend the LLM literature covers it.

---

## 4. Benchmarks, metrics, and what a fair comparison requires

### 4.1 Simulators

- **SUMO** (TraCI). The industry/standards default; every design-deployable study here uses it (Virtual Traffic Police, REG-TSC, all the ×Light family). Microsim + real OSM. Overhead is the *fairness* control for LLM: all baselines and LLM run in the same simulator with the same controls.
- **CityFlow** (KDD 2019, DOI 10.1145/3357384.3358028). Frame-stepable, ~10 000 veh/s; no PyPI wheel — source build only (or the Docker image the docs push). Used by CoLLMLight, HeraldLight, Traffic-R1 and most LLM papers *because* it is an in-sim call loop.
- **RESCO** (NeurIPS 2021 D&B): SUMO + OpenAI-Gym; **but note the paper's own headline result — "previous algorithms are not robust… when a distributed deep Q-learning approach is shown to outperform previously reported state-of-the-art".** Benchmarks are *styled* (grid/CAM/tier/scenario).
- **LibSignal** (arXiv 2211.10649; *Machine Learning* 113:5235–5271, 2024): cross-simulator equivalence across SUMO and CityFlow. **This is the fairness control any cross-simulator claim requires** — the authors' whole point is that models validated in one simulator are not referential to the other.

### 4.2 Metrics

Standard (all computable directly in either simulator):
- **AWT** average waiting time · **ATT** average travel time · **AQ** average queue length · **throughput** (vehicles/h penetrated) · **stops**.
- **Fairness CDFs** (p95 waiting time) rather than only means — HiLLTS does this and it is the cheapest way to expose starvation, which an orchestrator *will* cause at a cordon boundary.
- **Gridlock metrics** (the design's own claim): overflow events, vehicles-done vs (vehicles-in + left-in-network) deficit, recover time from inject→clear, number of nodes retained in the cordon, and versus a no-orchestrator ablation *and* a rule-only orchestrator ablation.

### 4.3 What fairness requires

1. **Same sim, same phase spec** (30 s green / 3 s amber / 2 s all-red is the Guangzhou/Hangzhou default in 2503.11739/2508.02344/2312.16044). Same vehicle counts and seed runtime.
2. **Update step matched to control frequency** — an LLM on a 1 s step in CityFlow has paid to re-decide 30× per green. Declare it; else you're comparing reasoning policy vs. actuation rate.
3. **Competent contemporary baselines:** CoLLMLight, Traffic-R1 (comms), Virtual Traffic Police (override), OverFlowLight (gridlock mechanism), plus **Varaiya MP** and a *rule-only two-tier* (fixed-time plan + rule overflow phases).
4. **Oversaturation sweep sized from the description**, to make gridlock reachable. the common Jinan/Hangzhou/CityFlow draws are light and rarely gridlock (Varaiya's Example 3 needs *all* links saturated; CoLLMLight's Jinan runs average ~30 veh/min per intersection); scale to v/c > 0.9 on closing links; a gridlock paper with no gridlock in its demand has nothing to show.
5. **Report realized LLM call rate and mean inter-call gap** for every method.
6. **5 seeds, report mean and CI.** A good share of the LLM-TSC papers in §1 report single runs; the means are not reproducible across seeds and you must not inherit that.

### 4.4 The two concrete traps

- **Node "pressure" with `max(0, ·)` is *efficient/projected* pressure** (2112.02336, 2112.10107). It is exactly as computation-bound as MP and strictly better behaved than plain pressure when a queue runs short, **but Varaiya's Theorem 2 does not cover it** — you may not claim MP stability. Cite Efficient-MP, and call the quantity "projected pressure".
- **Any demand without storage exhaustion never exercises the orchestrator.** *Every* draw must hit a full queue on a closing link at least once, or the demo becomes "LLM agents redo MP". Count and report gridlock/overflow events per run, alongside AWT.

---

## 5. Novelty verdict

### Already solved (don't restart, cite and build on)

- **Pressure is a solved cost function.** Varaiya 2013 proves stability when turn ratios and sat-flows are known, and Theorem 4 extends it to converging estimates. Building "pressure + LLM judge over the pressure ranking" is largely a repackaging, and **only proves novelty if you state that the LLM replaces the turn-ratio table** rather than replacing MP.
- **Per-intersection LLM agents.** About 40 % of the 26-hit corpus. LLMLight (2312.16044) is 3 years old; ProcessLight / DGLight / CuraLight are 2026.
- **Agent-to-agent communication.** Traffic-R1's async message-passing is explicit; CoLLMLight's neighbour graph is an implicit form.
- **Supervisor overrides locals.** Virtual Traffic Police (2026), HiLLTS' city layer (2026), TrafficGen's Supervisor (2026), iLLM-TSC's action auditor. A two-tier design is no longer a contribution in isolation.
- **Hold-vs-switch as an action.** MP is `argmax` over phases; hold vs switch is a partition of that action set. Calling it out as a fresh action space is a weak contribution on its own.
- **"LLM call budgets are a problem"** — everyone knows; Virtual Traffic Police and CoLLMLight both argue it. A latency table is table stakes, not a finding.

### Partially solved (and the source of whatever is citable)

- **LLM as *preempting* layer that takes control away from controllers rather than advising them.** Virtual Traffic Police is the closest, but it only *tunes parameters* — never takes over phase selection, and works on one intersection. An orchestrator that issues *phase-level* control over a *set of nodes* for a *bounded episode* is new.
- **Control hand-back.** Nothing found. The human "traffic officer takes over, then leaves" narrative is in 2601.15816 and in the OverFlowLight prompt, but no algorithmic hand-back (state contract with the local controller, hysteresis to re-arm, blameless abort) is published.
- **Gridlock as the *trigger*.** OverFlowLight triggers on a queue/spillback *count*, not on a blocked-cycle, and resolves within one intersection. A node-level gridlock detector with phase-override + release is new territory.
- **LLM restricted push grammar.** HeraldLight and others only shape JSON; nobody has published an orchestrator whose action set is restricted to *legal phase intervals/allowed turns at the affected nodes*.
- **Node agents surviving an orchestrator episode** (own-state from stored snapshot, stale replay, resume on hand-back). Not published.

### Genuinely open (if you can move here, it's publishable)

- **Coordinated escape from a joint deadlock** — which node yields, in what order, which movement is sacrificed. Give each node one priority ticket per k steps, the orchestrator verifies the chord is mutually satisfiable, execute in priority order.
- **Bounded-wait / deadlock-freedom.** Operative if you rebuild the action set: give the agent a *zone budget* (call only while Z > 0; Z regenerates at the Green peninsula rate), and the orchestrator is the only process that may take a node below 1. A draining argument is sketchable (the orchestrator is the liveness oracle; agents' budgets make local non-work-conservation bounded), but the arithmetic needs deriving, not hand-waving.
- **Hand-back contract** — hysteresis quota, convergence guard, blameless abort — with measured detection/recovery numbers over a *ledger* of tier:
  ±0.5 as "within 5 % of no-orchestrator baseline" are concrete.

---

## 6. Where the design is exposed (the three attacks a reviewer will make)

1. **"This is CoLLMLight plus a manager"** → answer: per-node pressure at 1–3 Hz versus CoLLMLight's 30 s/phase, plus a bounded orchestrator episode with hand-back; publish the cadence explicitly in the method section, not the appendix.
2. **"The manager is a rule, not an LLM"** → answer: two-tier pressure is not the LLM's contribution; the *coordinated escape order, movement sacrifice, and re-timing during the commit* are. Ablate the LLM (rules-only orchestrator) and show the delta.
3. **"Gridlock is never in your demand"** → answer: two-arc ring + narrow links + a ramp gore + a one-way ring on a segment, from the *first* experiment of the cycle/detection design; report overflow-event counts alongside AWT.

---

## References (full list)

1. Varaiya P. Max pressure control of a network of signalized intersections. *Transp. Res. Part C* 36:177–195 (2013). DOI 10.1016/j.trc.2013.08.014.
2. Levin MW. Max-Pressure Traffic Signal Timing: A Summary of Methodological and Experimental Results. *J. Transp. Eng. A* 149(4):03123001 (2023). DOI 10.1061/JTEPBS.TEENG-7578.
3. Wei H. et al. PressLight. KDD 2019. DOI 10.1145/3292500.3330949.
4. Wei H. et al. CoLight. CIKM 2019. DOI 10.1145/3357384.3357972.
5. Chen C. et al. MPLight. AAAI 2020. DOI 10.1609/aaai.v34i04.6172.
6. arXiv 2112.02336 Efficient Pressure · arXiv 2112.10107 Advanced-MP · arXiv 2407.01421 C-MP · arXiv 2511.00309 Transit-MP · arXiv 2505.05258 CV-MP.
7. arXiv 2610.32280 Decision Timescales in MP · arXiv 2610.32290 Regret-Based MP · arXiv 2610.31037 Com-MP.
8. arXiv 2607.22691 HiLLTS · arXiv 2601.15816 Virtual Traffic Police · arXiv 2508.02344 Traffic-R1 (ACL 2026, aclanthology 2026.acl-long.995) · arXiv 2503.11739 CoLLMLight · arXiv 2510.26242 REG-TSC.
9. arXiv 2609.22746 ProcessLight · arXiv 2604.25259 DGLight · arXiv 2604.23902 LLM-Aug-TSC · arXiv 2604.05663 CuraLight · arXiv 2604.05535 SignalClaw · arXiv 2604.17456 TrafficClaw · arXiv 2511.00136 HeraldLight · arXiv 2508.20818 cMALC-D · arXiv 2312.16044 LLMLight · arXiv 2407.06025 iLLM-TSC · arXiv 2403.08337 LLM-Assisted Light · arXiv 2401.00211 Open-TI · arXiv 2406.12360 UrbanLLM.
10. arXiv 2606.27381 OverFlowLight · arXiv 2508.09678 auction metering · arXiv 1811.11300 self-healing networks · Lämmer S., Treiber M., Rausch M. Inflow-regulating traffic light control to avoid queue-spillovers (2013).
11. arXiv 2210.10453 Tsitsokas & Kouvelas, two-layer MP + perimeter control (*TR-C*, 2023).
12. arXiv 2211.10649 LibSignal (*Machine Learning* 113:5235–5271, 2024) · Ault J., Sharon G. RESCO, NeurIPS 2021 Datasets & Benchmarks.
13. Zhang H. et al. CityFlow. KDD 2019. DOI 10.1145/3357384.3358028.
14. Frontiers in AI 2026, doi 10.3389/frai.2026.1894645, TRAFFICGEN (multi-agent LLM orchestration, single intersection).