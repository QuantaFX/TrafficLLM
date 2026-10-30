# STATE — read this first

Handoff note for continuing **TrafficLLM**. Written at push time; update it as you go.

## What this is

A SUMO demo where **one LLM agent controls each intersection**. Each agent sees its own
lane queues, spillback and neighbour headlines and picks a signal phase every 5 s. An
**Orchestrator** agent detects district gridlock, takes over a cordon, inserts escape
phases, and hands each node back once its gate passes. Setting is the DLSU / Taft Ave
grid, 9 signalised intersections + 24 arms.

Portfolio demo. Optimise for *impressive and demonstrable*, not for a paper.

## Layout

| Path | What |
| --- | --- |
| `SPEC.md` | the spec: node agent vs orchestrator, gridlock premise, Varaiya §4.5 blindness |
| `PROMPTS.md` | **tested** model payloads. `response_format.json_schema` + `strict`, `max_tokens` 90, no `why` in the hot path |
| `FRAMEWORK.md` | why plain `asyncio.gather` over `httpx` for the 9 agents; LangGraph only for the orchestrator |
| `core/contract.py` | `Phase`, `NodeObs`, `Intent`, `Frame`, `Grant`, `Controls` |
| `core/phases.py` | builds the phase tables — one through+left group per **opposing bearing** pair |
| `env/env.py` | owns SUMO (libsumo), signal FSM, metrics, `blocked_edges`, `frame` |
| `env/auth.py` | grant/cordon authority |
| `env/orchestrator.py` | detect → take over → escape phases → hand back |
| `llm/llm.py` | client, `digest`, `faça()` façade, stall guard |
| `server.py` | HTTP (UI files) + WS (frame stream) |
| `ui/` | the map. `ui/export_net.py` regenerates `ui/network.json` from the `.net.xml` |
| `assets/buildnet.py` → `assets/taft.net.xml` | hand-built net (netgenerate stubs deadlock) |
| `assets/gendemand.py` → `assets/demand.rou.xml` | weighted random-walk demand |

## Hard-won facts — do not rediscover these

- **SUMO silently drops a route file that is not sorted by `depart`.** No warning.
  `gendemand.py` sorts before writing. Symptom when broken: `departed 0`, `Loaded > 0`.
- Hand-built net, not `netgenerate` — generated stubs are 19 m and teleport.
  3 lanes per direction: lane 0 left-only, lane 1 through, lane 2 through+right.
- `dir="s"` must be in `CLASS`, plus `s`/`T`/`R`. Missing it left the net with 12 `t` links.
- Phases pair by **opposite bearing** (~180° apart), not shared destination — the latter
  starves dead-end arms and 15-approach nodes (columns share an x, so `B11` has 15 approaches).
- `movements` keys are `turn:<inbound edge>`, **not** human labels.
- `faça()` is the only apply path. Route everything through `LLM.tick()` or `applied` goes blank.
- `eligible()` includes the current phase; the façade enforces `min_green`.
- Websockets 17.3 answers an upgrade on a shared port with **HTTP 200**. HTTP and WS get
  their own ports (`--port`, `--ws-port`, UI derives `port + 1`, `?ws=NNNN` overrides).
- Write `response_format` `json_schema` `strict: true` — `structured_outputs.choice` is
  accepted and **silently ignored**.

## Known broken — fix before recording a demo

1. **Gridlock never forms.** `blocked` stays 0 at 22k vehicles; top out-edge fill 0.42
   against a 0.9 spill threshold. `_out()` divides by `length / 7.5`, which is too
   generous — occupancy is under-reported. Nothing downstream (detection, cordon, escape
   phases, hand-back) can fire until this is right. **This is the next fix.**
2. UI stream unverified since the port split — one clean run against a fresh sim needed.
3. `report/` is parked; its `template.html` chunker produces 0 TOC items.

## Running it

`LLM_KEY` in `.env` is a live router key (also in `.env.example`); rotate if it leaks.
SUMO lives at `C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe`; `SUMO_HOME` must be set.
`.venv` is Python 3.12 with `libsumo`. Install path on a fresh box is `uv pip install -r requirements.txt`.

```
.venv/Scripts/python.exe server.py --port 8800 --ws-port 8801   # UI on :8800
.venv/Scripts/python.exe -m pytest                              # tests
```
## Asset note

`assets/taft.net.xml` + `assets/demand.rou.xml` are the only files `env/env.py` and `run.py`
load. `assets/build_demand.py` is the scenario-aware generator (reads `assets/scenarios.json`)
and is the one to prefer; `assets/gendemand.py` is the simpler fixed-rate fallback that
produced the committed demand at `--veh 900 --end 2400 --len 8`. Both emit `depart`-sorted
row files — keep that property.
