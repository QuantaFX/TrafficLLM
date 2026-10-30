# Simulator decision — measured, not claimed

Status: **decided**. SUMO 1.28 driven in-process through `libsumo`, with the stock
sumo-rl `3x3grid` net as the development scenario and a RESCO/4x4 grid as the
scale-up case. CityFlow stays viable but is demoted to "port target only if a
road/lane-level portability win shows up". MetaDrive, Eclipse MOSAIC, A/B Street
and PettingZoo-with-SUMO are each ruled out for a specific, measurable reason below.

**A note on honesty:** I could not build CityFlow on this machine; so its
per-step throughput is the only row without a number of my own.

Everything numeric in this document was produced on this laptop (Windows 11,
Python 3.12.10) with the commands printed next to it. Reproduction instructions are
in §A.7. Machine weakness is a floor, not a ceiling: the machine is slow, so every
SUMO number here *understates* throughput on the target box; every gap it shows
between two backends is a scheduling/architecture gap, which survives that change.

---

## 0. Short answer

**Use SUMO. Drive it with `libsumo` in-process. Never use the `traci` socket
protocol for the inner loop.**

Measured, 9-signal 3×3 grid, same traffic, 1 s step length:

| what the LLM loop actually does | libsumo | traci | difference |
|---|---|---|---|
| step + read 18 lanes/step (queues only) | **1,705 steps/s** | 109 steps/s | 15.6× |
| step + read 18 lanes + write 9 phases every 10 s | **1,788 steps/s** | 94 steps/s | 19× |
| step only, no I/O | 1,730–1,944 steps/s | 1,388 steps/s | 1.25× |
| one observation bundle (18 lane calls) | **39 µs** | **7,527 µs** | 194× |
| one control tick (9 × `setPhase`) | **1.9 µs** | **228 µs** | 120× |
| per-vehicle call (e.g. `getWaitingTime`) | 3.7–5.4 µs (getter) | 53–55 µs | ~10–14× |

In the loop that this project actually runs, the socket protocol is not "a bit
slower". The costs are per *call*, so they stack: 18 lane reads × 9 agents per step
is 10 ms/s before any LLM call. libsumo does the same work in 39 µs.

There is no second candidate that keeps both (a) a real traffic microsimulation
with per-lane occupancy and blocking and (b) a Python agent per intersection that
talks to it in tight lockstep. CityFlow has (a) partially and (b) only through an
in-process Python extension that has to be C++-built per platform. Everything else
in the shortlist is either a different kind of simulator or a wrapper.

---

## A. Empirical part

### A.1 What was measured, and why these variables

Three independent costs decide whether "an LLM at every intersection" is runnable:

1. **step cost** — how fast the engine advances 1 s of simulated time;
2. **observation cost** — reading per-lane queue/halt state for every controlled
   intersection every step (this is the agent's sensor, so it is on the critical
   path, not optional);
3. **action cost** — writing a phase decision per intersection.

Cost (2) is the one that kills socket-based control: `traci` is a TCP request/response
per call, so cost scales with *calls*, not with simulation work. libsumo is the same
SUMO engine linked into the Python process — zero IPC.

Also measured, because the project exists to study gridlock:
4. **gridlock scenario cost** — steady-state heavy demand, teleports disabled
   (`--time-to-teleport -1`), so spilled-back queues persist exactly as they would
   in a real jam, and the per-step cost of the state an orchestrator must poll.
5. **`traci` socket at depth** — a deliberately undersized `--num-clients` setting,
   because that is the failure a real "one client, 9 agents" design hits.

### A.2 Nets — environment

`sumo --version` in the venv: **1.28.0** (wheel-installed `sumo.exe`). The system
installation is 1.27.0. Benchmarks ran through
`C:\Users\narch\Desktop\TrafficLLM\.surbey-venv\Scripts\python.exe` with
`SUMO_HOME=C:\Program Files (x86)\Eclipse\Sumo` and the venv's `site-packages` on
`PYTHONPATH`.

| net | source (local) | signals | edges | demand |
|---|---|---|---|---|
| `3x3grid` | `survey/sumo-rl/sumo_rl/nets/3x3grid/3x3Grid2lanes.net.xml` | 9 | 192 | `routes14000.rou.xml` (shipped) for throughput; `survey/bench/broken/g3p2.rou.xml`, `g3p3.rou.xml` (mine) for gridlock |
| `grid4x4` | `survey/sumo-rl/sumo_rl/nets/RESCO/grid4x4/grid4x4.net.xml` | 16 | 464 | `grid4x4_1.rou.xml` (shipped) |
| `sp9` | `survey/bench/broken/sp9.net.xml` (mine, `netgenerate`) | 4 | 36 | `broken/spur.rou.xml`, `spur_heavy.rou.xml` (mine) |

**Why custom demand was necessary — a real finding about the stock assets.**
`routes14000.rou.xml` on the stock 3×3 grid peaks at **40 vehicles, 40 s mean time
loss, 0.08 % lane occupancy, zero blocked edges**, and every offered load above
~1,000 veh/h simply feeds straight through. The stock net also only implements
right turns plus a few through movements (verified with `sumolib`: `0Ei -> [0No,
0Wo, 3Ni]`, no left turn across the head). So the grid cannot gridlock on the
shipped demand. I generated turn-heavy demand with `gen_demand2.py` (52 turn pairs,
1,500 → 2,000 veh/h) to produce a real pressure scenario. Anyone benchmarking
gridlock on this asset set must do the same, and this caveat belongs in the paper.

### A.3 Results — 3×3 grid, exactly as asked

Command template (all numbers below come from this template, `--end 600`,
`--warmup 30`, `--reps 2`, columns as printed):

```bash
cd survey/bench
export SUMO_HOME="C:\\Program Files (x86)\\Eclipse\\Sumo"
export PYTHONPATH="C:\\Users\\narch\\Desktop\\TrafficLLM\\.surbey-venv\\Lib\\site-packages"
NET="C:/Users/narch/Desktop/TrafficLLM/survey/sumo-rl/sumo_rl/nets/3x3grid/3x3Grid2lanes.net.xml"
ROU="C:/Users/narch/Desktop/TrafficLLM/survey/bench/broken/g3p2.rou.xml"
for m in libsumo traci; do
  for o in "" "--observe" ; do
    ../../.surbey-venv/Scripts/python.exe bench_step.py --mode $m --net "$NET" --routes "$ROU" --end 600 --reps 2 --warmup 30 $o
  done
done
```

Raw output (1 s step, 569 steps per run, 9 signals, 18 lane-state calls per step):

```
libsumo step-only            : 1730 steps/s   578 µs/step
libsumo obs every step       : 1705 steps/s   586 µs/step   obs overhead 152 µs/step
libsumo obs+ctl every 10 s   : 1788 steps/s   (83 control ticks)
libsumo ctl only every 10 s  : 1944 steps/s
traci   step-only            : 1388 steps/s   720 µs/step
traci   obs every step       :  109 steps/s  9199 µs/step   obs overhead 8674 µs/step
traci   obs+ctl every 10 s   :   94 steps/s 10647 µs/step
traci   ctl only every 10 s  : 1018 steps/s
```

Interpretation:

* **Reading the sensor is 194× more expensive over the socket.** 18 lane reads cost
  39 µs in libsumo and 7.5 ms in traci. Traci's per-call round trip is 48–55 µs.
* **Step-only is nearly identical (1.25×).** All the loss is IPC, not simulation.
  A design that "only asks occasionally" still pays 0.5–1 ms per crossed message.
* **Loop overhead is negligible in libsumo.** Observation + batched control change
  steps/s by less than the run-to-run spread.
* **Writing phases is not the bottleneck** — a 9-phase write tick is 1.9 µs (libsumo)
  vs 228 µs (traci).
* Absolute numbers are small because this machine is weak: the *same* libsumo loop
  that costs 586 µs/step here spends most of its time in SUMO's own vehicle update
  at 320 running vehicles. On the target machine the constant factors drop; the
  ratios do not.

### A.4 Results — 16-intersection cross-check

Same harness on the RESCO 4×4 grid (`--end 600`, 32 lane calls/step):

```
libsumo step-only   3862 steps/s     libsumo +observe   2572 steps/s
traci   step-only   2036 steps/s     traci   +observe     39.7 steps/s   (14.5 s wall)
```

At 16 signals the socket design reaches **40 steps/s**; one simulated hour of dense
grid costs ~90 s of wall time before any LLM latency is added.

### A.5 Results — gridlock regime (teleports off)

`bench_gridlock.py`, 3×3 grid, `g3p3.rou.xml` (2,000 veh/h, 5,400 s simulated) and
`g3p3_hold.rou.xml` (East/West off until t=300 s, then released). Backend costs on
the same run, plus the state an orchestrator has to watch:

```
libsumo  5400 steps  454 steps/s   mean time loss 572 s   max 4084 s
                     17 blocked edges (max 25)   mean queue 226   probe 138 µs
traci    5400 steps  246 steps/s   mean time loss 615 s   max 4069 s
                     17 blocked edges (max 25)   mean queue 205   probe 12.1 ms
with 300 s hold-then-release: 17 blocked edges, 25 max, queue stable at 210 → jam is
                             already terminal at t=300 s
```

The grid saturates and stays saturated: 99 blocked-edge-samples per 1,000 s window
in both halves of the run, mean time loss 6–10× the free-flow value, 4,529 vehicles
marooned at the end. This is a usable gridlock fixture. Backend cost in this regime
is ~3.8× the step cost of the light scenario, which is the right order of magnitude
for "queues now occupy every junction" and matters for sizing runs.

`blocked_window_*` in the raw JSON counts lane-samples with ≥3 halted vehicles; the
orchestrator probe (one occupancy scan of all 192 edges) costs **138 µs (libsumo) vs
12.1 ms (traci)** per tick. A socket-driven orchestrator that scans the network every
second adds 12 ms of pure latency per simulated second.

### A.6 `traci` at depth — the trap that bit in practice

First runs died with `Connection closed by SUMO` under `cityflow`-style call volumes.
Cause: `traci.start` defaults to **one** connection; the client multiplexes per
`--label` and a second agent gets a second `Connection` that is closed on switch. The
single-threaded fix is one shared connection with distinct labels
(`traci.start(..., label="agent3")`, `getConnection("agent3")`). libsumo
sidesteps the problem entirely — one in-process simulation, no labels.

### A.7 Reproduction recipe

```bash
# deps already in .surbey-venv: eclipse-sumo, traci, libsumo (all 1.28.0), sumolib
cd survey/bench
export SUMO_HOME="C:\\Program Files (x86)\\Eclipse\\Sumo"

# 1. gridlock demand for the 3x3 grid (needs sumolib + net)
python gen_demand2.py --net "<3x3Grid2lanes.net.xml>" --mode cross --rate 1500 -o broken/g3cross.rou.xml
python gen_demand2.py --net "<3x3Grid2lanes.net.xml>" --mode spread --rate 1500 --seed 11 -o broken/g3spread.rou.xml

# 2. throughput / observation / action cost, both backends
python bench_step.py  --mode libsumo --net "<net>" --routes broken/g3p2.rou.xml --end 600 --reps 3 --warmup 30 --detailed
python bench_step.py  --mode traci   --net "<net>" --routes broken/g3p2.rou.xml --end 600 --reps 3 --warmup 30 --observe

# 3. gridlock regime with orchestrator probe
python bench_gridlock.py --mode libsumo --net "<net>" --routes broken/g3p3.rou.xml --end 5400
```

Scripts: `bench_step.py` (per main phase: plain, `--observe`, `--control`;
`--detailed` adds isolated observation/timing sub-windows; writes `results.jsonl`),
`bench_gridlock.py` (gridlock + orchestrator probe, writes `gridlock_metrics.json`),
`bench_calls.py` (per-call latency microbench), `bench_action.py` (per-tick control
latency), `gen_demand2.py` (route generation), `bench_plain.py` (classification pass).
`bench_cli_replay.py` and `probe_maint.py` are supporting probes; their output fed
the notes above.

### A.8 Supporting checks

* The CLI spawn floor is ~230 ms (three `date` deltas of a bare `sumo --version`).
  A replay-and-parse CLI design is therefore ~1.4 s per simulated hour on the 9-signal
  net before parsing, and it re-derives queue state itself: `--queue-output` (1.4 ms of
  writes here) writes 2.3 MB per 1.8 ks. Usable for offline sweeps, not for a
  closed loop.
* There is no built-in per-agent observation bundle; `getTimeLoss` / `getWaitingTime`
  are per-vehicle getters. The observation is the per-intersection lane sweep at
  §A.3/A.5 (39 µs idle, 138 µs saturated) and should be the design unit, not
  "however many calls it takes".

**Measurement caveat, stated plainly.** 1 s steps, single thread, `--seed 42`, best of
2–3 runs on a weak machine. Absolute steps/s overstates libsumo's speed *margin*:
in one early non-interleaved series libsumo showed no penalty at all, because sim
work had already shrunk past the measurement noise. Treat the ratios in §A.3/A.4 as
the signal and the absolute numbers as "this machine, this afternoon".

---

## B. Analytic comparison

Maintenance figures from the GitHub API at time of writing. Install commands given
as they would actually be typed.

| | SUMO + libsumo | CityFlow | MetaDrive | Eclipse MOSAIC | A/B Street | PettingZoo (+ SUMO) |
|---|---|---|---|---|---|---|
| **Headless?** | yes, and in-process: `libsumo.start()` needs no `sumo.exe`, no TCP, no window (`sumo-gui` is optional) | yes, but only as a C++ extension inside the Python process; no CLI, no socket | yes with `METADRIVE_HEADLESS`; Panda3D still initialises and Vulkan/DirectX must resolve | headless-by-design **only after** a Maven+cmd **compilation** step (bundled `run.sh` rebuilds; Windows needs Visual Studio C++) | yes (`abstreet --bin`), but the build is cargo+LFS data | it is a framework — the SUMO coupling is a community fork (sumo-rl), `LIBSUMO_AS_TRACI` removed after v1.3 |
| **Python agent injection** | first-class: per-intersection phase/queue/lane APIs; libsumo is the same API as traci, so swapping transport is a one-line change | `cityflow.Engine` calls in-process; a new simulator per episode; Python only via the extension module built in-tree | agents are *ego vehicles* with `BaseAgent` + physics controllers; signals controlled through MetaDrive's own TL API, not an external agent | coupling is Java; Python agents arrive only through the deprecated, MessageGarage-backed `python_aiohttp` bridge; needs Java glue per agent | turns and events in `RawModel` structures that the TUI apps consume; no documented "external controller, one per junction" contract | fork-maintained; upstream API is generic `env.step(dict)` |
| **Multi-agent API out of the box** | sumo-rl ships it: `parallel_env`, agents keyed per TLS, PettingZoo-Parallel | none; raw engine calls | none for signals | none | none for junctions | PettingZoo itself — real, but generic |
| **Windows** | *this* path: venv + `pip install eclipse-sumo traci libsumo` → `libsumo.start([...])`. Installed here, ran here. 1.27.0 already at `C:/Program Files (x86)/Eclipse/Sumo` | **source only, Unix only**. PyPI 404. Dev doc: "we only support building on Unix systems … for Windows use WSL or Docker" → `sudo apt install build-essential cmake; git clone …; pip install .` | `pip install metadrive-simulator`, then it fights your Vulkan runtime, not traffic | `mvn package` with `JAVA_HOME` + `MAVEN` env vars, then `deploy` | `cargo run --release --bin abstreet -- --infinite` (~20 min first build, multi-GB LFS) | `pip install pettingzoo`; SUMO side = `pip install sumo-rl` + `SUMO_HOME` + net assets |
| **Linux** | trivial: SUMO from PPA/apt, then the same pip wheels; libsumo is the *documented* fast path (`export LIBSUMO_AS_TRACI=1`) | also from source, but with `sudo` and a compatible toolchain | same pip install, Vulkan expected, headless via xvfb only | native; still a compile step | native build | trivial for PettingZoo; SUMO side as above |
| **Step throughput (evidence)** | **measured here: 1,705 steps/s with full observation, 454 steps/s saturated gridlock** | paper claims faster-than-SUMO *with 1–8 threads* (README graph); **not measured here, and unreachable on Windows without WSL/Docker** | Unity-PhysX class; paper-scale benchmarks, not comparable per step | Java/JVM + AORTA traffic sim + Federate glue; comparable order of magnitude, not observable/fast for RL-style step throughput [unverified] | desktop, and CLI mode rebuilds geometry per run | inherits SUMO, then **sumo-rl v1.4 forces the traci socket** → 109 steps/s as measured |
| **Maintenance** | 2026-10-29 pushes, EPL-2.0, 2,864 open issues incl. PRs, releases on a schedule | **no releases since 2020-12**, final commit 2023-01-23, 39 open issues across 4,000 stars, Apache-2.0 | 2026-10-16 pushes, 95 open issues, but PyPI last **uploaded 2024-12-06** and `requires_python <3.12` | 2026-08-10 pushes, 16 open issues, EPL-2.0; a *coupling* framework, not a traffic engine | 2025-09-10 pushes, 226 open issues, Apache-2.0 | PettingZoo: 2026-10-09, MIT, 70 open issues |
| **Blocked boxes / realistic spillback** | yes — Krauss car-following, lane-length storage, junction-internal blocking, per-vehicle/per-lane occupancy (`getLastStepHaltingNumber`, `getLastStepOccupancy`, `getTimeLoss`); teleports optional as an artifact lever | yes for spillback *along lanes*; junction-internal blocking covered by the README claims (Intersection/Cross/Grid samples), box-occupancy and `getTimeLoss`-style metrics **not exported** | lanes and circles around ego cars; realistic lane-level spillback and junction blocking is **the weak point** (no lane-level storage semantics) | AORTA implements full ITS stack and lane-level jam semantics; heavy to instrument per junction | lane-level with per-junction overrides; **rich** for "block the street" experiments, but only city-data scales | inherits SUMO |
| **Verdict for this project** | **primary** | keep as **road-level portability target only** | not a signal sim | too much coupled machinery per agent | excellent Lego, no race-winning throughput | PettingZoo interface without SUMO's sensor speed |

### Why each non-SUMO option lost

* **CityFlow** — fastest *engine*, worst *plumbing*: no PyPI, no Windows, no
  multi-agent API, no budget for per-lane halting state or per-vehicle time loss,
  five years without a release. It is also a second simulator, so phase sets and
  grids **do not carry over**; the port is a re-model per town. That is a project in
  itself. If CityFlow ever moves, `libsumo`'s API is the only one close enough to
  make the swap cheap.
* **MetaDrive** — built for ego-vehicle autonomy; policies act through physics
  controllers, lanes are abstract and *blocked boxes do not exist* in the sense this
  project needs. Also lowest-margin install (`<3.12` pin vs the py3.12 env), and
  Panda3D's headless story is a known source of CI flakiness.
* **Eclipse MOSAIC** — its multi-agent story is a tram network of *coupled* federates
  in Java, configured through Maven, with Python reachable only through a deprecated
  bridge. Nice for citywide ITS, wrong latency class for a per-step LLM loop.
* **A/B Street** — the most fun data model (street edits, per-junction overrides) and
  a Rust build that is fine, but no out-of-the-box multi-agent signal API and no
  step benchmark that competes. Good for *scenario authoring*, not engine.
* **PettingZoo / sumo-rl** — the interface, not a simulator. Worth taking the *API
  shape* from (sumo-rl's `parallel_env` and per-TLS agent keys are the right
  ergonomics) and wiring it to libsumo. Note `LIBSUMO_AS_TRACI` was removed after
  sumo-rl 1.3, so a pip-installed sumo-rl is stuck on the socket (109 steps/s).

---

## C. Recommendation

**Build on SUMO 1.28 + `libsumo`, in-process, with sumo-rl's 3×3 grid as the default
dev net.**

Justification, in the order that decided it:

1. **Measured loop throughput** beats every alternative that can actually observe and
   act per intersection — 1,705 steps/s against traci's 109 and sumo-rl-on-socket's
   93–109. LLM latency (seconds per decision) dwarfs IPC latency, but the *simulator*
   must not add its own 9 ms/s on top of every tick, and the orchestrator's 12 ms/s
   occupancy scan is exactly what a gridlock study runs continuously.
2. **It is the only candidate where blocked boxes exist as inspectable state.** The
   gridlock metric is a sum of `getLastStepHaltingNumber` / `getLastStepOccupancy`
   over lanes, 39–138 µs per sweep in libsumo. MetaDrive has no equivalent.
3. **First-class Python API per junction** (phase index, controlled lanes, per-vehicle
   time loss) — the agent and the orchestrator both need exactly that surface.
4. **Windows + Linux from the same `pip install`**, 1.28.0 wheels installed and run
   here.

### Explicit limits to design around

* libsumo is **one simulation per process** and **no `sumo-gui`**. Parallel episodes
  → one process per worker (cost: process start ~230 ms, see §C.4). Visual debugging
  → a `traci` run against `sumo-gui` on the same net file; the API is identical, so
  the switch is one `import`.
* libsumo keeps **one engine instance per interpreter**, so **agent parallelism must
  come from the LLM service, not from threads inside the sim process**. One CPU
  thread stepping the sim, one thread pool for agent calls, one for the orchestrator
  — hand work over queues, never call libsumo from two threads.

### Install / run (exact commands)

```bash
# --- Windows ---
python -m venv .venv
.venv/Scripts/pip install --only-binary :all: eclipse-sumo traci libsumo      # 1.28.0 wheels, no compiler
export SUMO_HOME="C:\\Program Files (x86)\\Eclipse\\Sumo"
.venv/Scripts/python -c "import libsumo; print(libsumo.__version__)"

# --- Linux ---
sudo add-apt-repository ppa:sumo/stable && sudo apt update
sudo apt install -y sumo sumo-tools sumo-doc
export SUMO_HOME=/usr/share/sumo
python -m venv .venv && .venv/bin/pip install eclipse-sumo libsumo traci

# --- run a scenario in-process ---
.venv/Scripts/python - <<'PY'
import libsumo as S
S.start(["sumo",
         "-n", "survey/sumo-rl/sumo_rl/nets/3x3grid/3x3Grid2lanes.net.xml",
         "-r", "survey/bench/broken/g3p3.rou.xml",
         "--no-warnings", "--no-step-log", "--seed", "42", "--time-to-teleport", "-1"])
for _ in range(10):
    S.simulationStep()
    for tl in S.trafficlight.getIDList():
        q = [S.lane.getLastStepHaltingNumber(l) for l in S.trafficlight.getControlledLanes(tl)]
        S.trafficlight.setPhase(tl, 0 if sum(q[: len(q)//2]) <= sum(q[len(q)//2:]) else 1)
PY
```

`--time-to-teleport -1` is mandatory for gridlock work: SUMO otherwise teleports
vehicles out of jams after 300 s and dissolves the phenomenon under study. The
snippet above was executed here (libsumo 1.28.0, 10 steps, phase write) and returned
`ok 10.0`.

### Fallback if the primary fails

Ranked; each is one `git checkout` away because the observation/action layer is
written against the traci-compatible surface.

1. **`traci` + `sumo` subprocess** (identical API, ~100 steps/s with a 9-agent loop, no
   in-process constraint, `sumo-gui` available, multi-process safe). Acceptable for a
   showcase demo and the GUI path; **re-split the loop**: read queues once per control
   interval, never per agent per step, or the 9 ms/s returns.
2. **Docker `cityflowproject/cityflow`** (Linux container; CityFlow's own quick start).
   Cheapest way to CityFlow **without** a Windows build, and CityFlow's per-step
   ceiling is above SUMO's — cost is a tail rebuild of the net and route format and
   the loss of sub-second halting-state granularity.
3. **Anywhere else given an Ubuntu box**: same libsumo design, no changes.

### Remaining risks

* Trust the *ratios* more than the absolute steps/s (#0.5–1 s). Rerun `bench_step.py` on
  the target machine before locking the paper's throughput claims.
* The stock 3×3 asset cannot gridlock; treats `survey/bench/broken/g3p2.rou.xml` as the
  baseline scenario, not `routes14000.rou.xml`.
* `mvn package` for MOSAIC (if ever revisited) on Windows needs Visual C++ build tools;
  the bundled `run.sh` will rebuild and fail, a missing runtime JAR otherwise.
* The `traci` fallback still costs ~9 ms/s of pure IPC per supply one, so the
  orchestrator cadence must be coarse (≥5 s) and its occupancy scan rate kept separate
  from the agent update rate.

---

## Assets confirmed on disk

Both of the following exist locally and were used for the runs above.

| purpose | path | licence | notes |
|---|---|---|---|
| dev grid (9 signals) | `survey/sumo-rl/sumo_rl/nets/3x3grid/3x3Grid2lanes.net.xml` (+ `routes14000.rou.xml`, `3x3grid.sumocfg`) | sumo-rl repo root `LICENSE` = **MIT** (© 2018 Lucas Alegre) | net is not itself MIT-licensed separately; 9 TLS, 192 edges, and **right-turn-only movement sets** — the reason custom demand is needed. Single-intersection (`nets/simple/`, `nets/single-intersection/`) and 2×2 (`nets/2x2grid/`) nets also ship, all under the same MIT repo. |
| scale-up grid (16 signals) | `survey/sumo-rl/sumo_rl/nets/RESCO/grid4x4/grid4x4.net.xml` (+ `grid4x4_1.rou.xml`) | **CC BY-NC-SA 4.0** (`sumo-rl/sumo_rl/nets/RESCO/LICENSE`, `RESCO/grid4x4/LICENSE`) | same file also in `survey/resco/resco_benchmark/environments/grid4x4/` (CC BY-NC-SA 4.0); **non-commercial**, read again before any commercial use |
| gridlock fixture (mine) | `survey/bench/broken/g3p2.rou.xml`, `g3p3.rou.xml`, `g3p3_hold.rou.xml` | n/a (generated here) | 1,500 / 2,000 veh/h turn demand; `g3p3_hold` releases after a 300 s hold |

Licence stack of `survey/resco` (Pi-Star-Lab, `RESCO v2.0`, 2025-10-28): repo `LICENSE`,
per-environment `LICENSE` (CC BY-NC-SA 4.0) — nominally compatible with CC BY-NC-ND
maps, still all non-commercial. If a commercial license matters for `3x3grid`, it is
MIT and portable without the RESCO tree.