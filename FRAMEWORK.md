# LangGraph vs. the current approach

Measured on this machine, `langgraph` 1.2.18, against the live `flash` endpoint. Same prompt, same
nine concurrent node calls, same schema. Three runs each.

| variant | median tick (9 nodes) |
|---|---|
| plain `asyncio.gather` + `httpx` | 8.95 s / 17.10 s / 9.55 s |
| LangGraph graph, no checkpointer | 8.40 s / 16.39 s / 14.63 s |
| LangGraph + `InMemorySaver` | 8.18 s / 9.66 s / 9.82 s |
| `graph.compile()` | 0.9 ms |
| `import langgraph.graph` after import | 1 additional module |

**Overhead is inside the noise.** The spread between runs (8–17 s) is far wider than any difference
between the three variants, because the endpoint's own queueing dominates. LangGraph costs
essentially nothing at this cadence.

Import weight is smaller than the reputation suggests, but it is not free: `langgraph` pulls 63 MB
into `site-packages`, including `langsmith`, `langchain_core`, `langchain_protocol`, `langgraph_sdk`,
`orjson`, `requests`, `jsonpatch`, `tenacity`.

## What LangGraph would actually give you

Not latency — the loop is 9 HTTP writes and a dict. The two things it genuinely offers:

1. **The takeover/hand-back transition is already in the node-and-edge shape.** `who = local |
   orchestrator` with a control-grant record is a `Send`/edge-guard pattern. Routing control by
   `gen` is what LangGraph is *for*.
2. **`interrupt` + checkpoint** would make hand-back constraints survive a restart. Today the
   re-stated constraints live in a dict; with a checkpointer they live in the checkpoint, and
   `get_state` recovers them.

## What it would cost

- **The agent will be tempted to read "LangGraph" as "ReAct agent with tools"** — the internet is
  full of ReAct TSC papers (Traffic-R1, VLMLight, CoLLMLight) that add a reflection/tool loop per
  intersection. In LangGraph that is cheap to build and it will blow the 5 s tick, because
  reflection is 2–4 extra model calls per node per tick. Recommend the LangGraph shape **without**
  a ReAct loop: a graph node that makes one structured call.
- **9 independent agents are 9 writes, not a graph.** If the whole thing were plain deferral —
  every node calls every neighbour — LangGraph adds ceremony. Its value starts at the orchestrator.
- **Checkpointer discipline.** A checkpoint per tick at 9 nodes is fine in memory; with
  `PostgresSaver` it becomes a DB write per tick, which *will* show up. Use `InMemorySaver` and
  persist only at episode boundaries.

## Verdict

**Use LangGraph, but only for the upper layer.** Concretely:

- inner loop — plain async functions. `gather` the nine calls, parse, façade, apply. No graph, no
  tools, no reflection.
- orchestrator + control transfer — LangGraph. Nodes for `detect`, `diagnose`, `apply_step`,
  `gate`, `handback`; edges guarded on `gen`; `interrupt` at hand-back; `InMemorySaver`, snapshots
  at episode boundaries only.

If the scoreboard is "does it run and look impressive", plain async throughout is the lower-risk
choice and loses nothing measurable. If it is "a portfolio piece someone reads", the graph on the
orchestrator is the part worth showing — it is where the actual novelty lives, and the novelty is
the only reason this project is not max-pressure with an API bill.

**Do not** put the node agents through LangChain message objects or an agent executor. The digest
is already JSON; wrapping it in `HumanMessage` and unwrapping the response adds nothing and costs
tokens.