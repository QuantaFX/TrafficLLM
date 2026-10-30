"""Tick loop + websocket server.

One asyncio loop owns the sim: 1 s steps, node agents on a 5 s tick, orchestrator on a
2 s tick. LLM calls run concurrently inside the tick and the loop waits for them - the
tick is a latency budget, not best-effort.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import socket
import websockets

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

from core.contract import Controls, Frame
from env.env import Env
from env.orchestrator import Orchestrator
from llm.llm import LLM, faça

ROOT = os.path.dirname(os.path.abspath(__file__))
AGENT_TICK = 5
ORCH_TICK = 2
BASE_CYCLE = 25.0


class Sim:
    def __init__(self, scenario, seed, llm_on):
        self.env = Env(scenario, seed)
        self.llm = LLM(llm_on)
        self.orch = Orchestrator(self.env, self.llm)
        self.controls = Controls(seed=seed, scenario=scenario, llm=llm_on)
        self.clients: set = set()
        self.stop = False
        self.intents: list[dict] = []
        self._busy = False

    async def run(self):
        self.env.start()
        while not self.stop:
            if self.controls.paused or not self.clients:
                await asyncio.sleep(0.2)
                continue
            await self._tick()
            await asyncio.sleep(BASE_CYCLE / max(0.5, self.controls.speed))

    async def _tick(self):
        env = self.env
        if self._busy:
            return
        self._busy = True
        try:
            if env.t % AGENT_TICK == 0:
                await self._agents()
            if env.t % ORCH_TICK == 0:
                self.orch.step()
            for n, ph in list(env.parked_phase.items()):
                if n not in self.orch.auth.grants:
                    env.parked_phase.pop(n)
            frame = env.step()
            if frame:
                await self.broadcast({"type": "frame", "frame": _json(frame)})
        finally:
            self._busy = False

    async def _agents(self):
        env, gen = self.env, self.orch.auth.gen
        asks = [o for o in env.obs_all() if not o.parked]
        if not asks:
            return
        t0 = time.perf_counter()
        done = await self.llm.tick(
            asks, {o.node: {p.idx: o.pressure(p) for p in env.tables[o.node]} for o in asks})
        by = {o.node: o for o in asks}
        out = []
        for i in done:
            o = by.get(i.node)
            if o is None or o.locked_by:
                continue
            self.orch.auth.note(i, gen)
            if i.applied != "ok":
                out.append(i.to_dict())
                continue
            i = faça(o, i, o.last_switch, o.t,
                                 {p.idx: o.pressure(p) for p in env.tables[i.node]})
            if i.applied == "ok" and i.action == "switch" and i.target != o.phase:
                env.force_phase(i.node, i.target)
            out.append(i.to_dict())
        self.intents = (self.intents + out)[-200:]
        if out and time.perf_counter() - t0 > AGENT_TICK * 0.8:
            print(f"[warn] agent tick {time.perf_counter() - t0:.1f}s over budget", flush=True)

    async def broadcast(self, msg):
        if not self.clients:
            return
        data = json.dumps(msg)
        await asyncio.gather(*(c.send(data) for c in list(self.clients)), return_exceptions=True)

    async def handler(self, ws):
        self.clients.add(ws)
        try:
            await ws.send(json.dumps({"type": "hello", "control": asdict(self.controls)}))
            async for raw in ws:
                await self._control(json.loads(raw))
        except Exception:
            pass
        finally:
            self.clients.discard(ws)

    async def _control(self, m):
        k, v = m.get("type"), m.get("value")
        if k == "speed":
            self.controls.speed = max(0.5, min(8.0, float(v)))
        elif k == "paused":
            self.controls.paused = bool(v)
        elif k == "llm":
            self.controls.llm = bool(v)
            self.llm.enabled = bool(v) and bool(self.llm.client.headers)
        elif k == "restart":
            await self.restart(m.get("scenario") or self.controls.scenario, m.get("seed"))

    async def restart(self, scenario, seed):
        self.env.close()
        self.env = Env(scenario, int(seed or self.controls.seed))
        self.orch = Orchestrator(self.env, self.llm)
        self.intents = []
        self.env.start()
        await self.broadcast({"type": "reset"})


def _json(f: Frame) -> dict:
    d = asdict(f)
    return d


async def main(args):
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    sim = Sim(args.scenario, args.seed, not args.no_llm)
    # HTTP and WS get their own port. Sharing one socket does not work: connections land
    # on whichever accept loop reads first, so the WS client gets an HTTP 200.
    server = await websockets.serve(sim.handler, args.host, args.ws_port, max_size=None)
    print(f"serving ws://{args.host}:{args.ws_port}/ws  ui http://{args.host}:{args.port}/", flush=True)
    http = await asyncio.start_server(_http, args.host, args.port)
    task = asyncio.create_task(sim.run())
    try:
        await asyncio.gather(task, http.serve_forever())
    finally:
        sim.stop = True
        http.close()
        sim.env.close()
        await sim.llm.aclose()


async def _ws(sim, reader, writer, head):
    """Complete a WS upgrade by hand and run the sim handler on the socket."""
    headers = {}
    while True:
        line = (await reader.readline()).decode()
        if not line.strip():
            break
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
    ws = websockets.ServerProtocol()
    ws.connection_made(writer)
    keep, writer = ws, ws.transport
    ws.connection_lost = lambda exc: None
    await keep.handshake(head.decode().strip(), headers, "")
    await sim.handler(keep)


async def _http(reader, writer):
    """Serve ui/ so one process runs the whole demo."""
    try:
        line = (await reader.readline()).decode()
        parts = line.split()
        path = parts[1].split("?")[0] if len(parts) > 1 else "/"
        path = "/index.html" if path in ("/", "/ws") else path
        full = os.path.join(ROOT, "ui", path.lstrip("/"))
        if not os.path.isfile(full):
            writer.write(b"HTTP/1.1 404 NF\r\n\r\n")
        else:
            body = open(full, "rb").read()
            ctype = {"html": "text/html", "js": "text/javascript", "css": "text/css"}.get(
                full.rsplit(".", 1)[-1], "application/octet-stream")
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\n"
                         f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        await writer.drain()
    finally:
        writer.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--ws-port", type=int, default=8801)
    ap.add_argument("--scenario", default="baseline")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-llm", action="store_true")
    asyncio.run(main(ap.parse_args()))