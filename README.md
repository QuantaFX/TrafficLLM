# TrafficLLM

Every intersection on a Taft Ave / DLSU grid is driven by its own LLM agent. Each one sees
only its own lanes and its neighbours' headlines, and picks a phase every 5 s. Locally
sensible agents deadlock each other into gridlock; a supervising **Orchestrator LLM** then
detects the cycle, seizes the district, inserts escape phases, meters the feeders, and hands
each intersection back once its gate passes.

Portfolio demo. The point is watching individually reasonable decisions produce a jam, and
watching a supervisor with a district-wide view dismantle it.

## Run

```bash
uv venv .venv
VIRTUAL_ENV=.venv uv pip install -r requirements.txt
cp .env.example .env          # put your key in LLM_KEY
python server.py --port 8800 --ws-port 8801   # http://127.0.0.1:8800/
```

Requires SUMO 1.28+ ([install](https://sumo.dlr.de/docs/Downloads.php)); set `SUMO_BIN` if it
is not on the default path.

The UI and the frame stream are on **separate ports** (the UI derives `--ws-port` as
`--port + 1`; override with `?ws=NNNN`). They cannot share one: websockets 17 answers an
upgrade on a shared listener with a bare HTTP 200.

`--no-llm` runs it deterministically with no model calls. `--scenario gridlock` starts loaded.

## How it works

- **1 s** sim step. Node agents decide every **5 s**, orchestrator every **2 s**.
- All nine calls happen concurrently; the loop waits on them, so the tick is a hard latency
  budget (measured ~4.6 s at nine-way concurrency).
- `json_schema` + `strict` on the action, with an enum rebuilt each tick from the legal
  phases only. A façade rejects early switches, wrong phases and thrash.
- Three silent ticks flag a node stalled and hold its last action. Six drop it, and the UI
  shows it frozen.
- Every decision, façade hit and grant is logged, so any guard firing replays from the digest.

The novelty is the gridlock trigger plus phase-level takeover and hand-back. Per-node LLM
control is published (CoLLMLight); the supervisor tier is not aimed at deadlock anywhere.

## Layout

| path | role |
|---|---|
| `env/env.py` | SUMO, signal state machine, observation, metrics |
| `env/auth.py` | control-grant layer, generation counters, hand-back gate |
| `env/orchestrator.py` | detector, escape, metering, release |
| `env/baselines.py` | fixed-time and max-pressure comparators *(planned — not written yet)* |
| `llm/llm.py` | client, schemas, digest, façade, stall guard |
| `core/` | contract, phase tables |
| `ui/` | map UI (toggleable tabs, hover cards) |
| `tests/` | loop, façade, race, orchestrator |

## Honest limits

An LLM-only lower layer loses to max-pressure on raw average wait; it is compared against it
rather than hidden. If gridlock never forms the demo is just "LLM agents redo max-pressure",
which is why blocked-arc count is always on screen.