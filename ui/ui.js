/* TrafficLLM map UI.
 *
 * Renders only what a frame says (core/contract.py). Inbound: {type:'frame', frame:{t, nodes[],
 * vehicles[], edges[], district, stats, intents[]}}. Outbound: {type:'control', ...} for every
 * control. SUMO's y axis points north, so screen y is flipped.
 */
"use strict";

const NS = "http://www.w3.org/2000/svg";
// WS lives on its own port (httpPort + 1). A ?ws=NNNN override wins for odd setups.
const WS_URL = (() => {
  const q = new URLSearchParams(location.search).get("ws");
  if (q) return `ws://${location.hostname}:${q}/ws`;
  const p = Number(location.port) || 80;
  return `ws://${location.hostname}:${p + 1}/ws`;
})();
const RECONNECT_MS = 1500;
const FILL_RAMP = [  // [fill, rgb] — cartographic congestion ramp
  [0.00, [39, 103, 73]],
  [0.45, [111, 143, 47]],
  [0.72, [212, 167, 44]],
  [0.88, [221, 107, 32]],
  [1.00, [197, 48, 48]],
];
const INTENT_BLOCKS = 9;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  ws: null, connected: false, everConnected: false, retry: 0,
  net: null, nodeById: new Map(), view: null,
  last: null, lastT: null,
  hoverId: null, pinId: null,
  feed: [], statsHist: [], orch: { state: "idle", id: "" },
  controls: { speed: 2, paused: false, llm: true, scenario: "gridlock" },
  open: new Set(),
};

/* ---------------------------------------------------------------- small utils */

function h(tag, attrs, parent) {
  const el = document.createElementNS(NS, tag);
  for (const k in attrs || {}) el.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(el);
  return el;
}
function hash(s) {
  let x = 2166136261;
  for (let i = 0; i < s.length; i++) x = Math.imul(x ^ s.charCodeAt(i), 16777619);
  return x >>> 0;
}
const attr = (el, k, v) => el.getAttribute(k) !== v && el.setAttribute(k, v);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function cls(el) {
  el.setAttribute("class", [...el.classList].join(" "));
}
function fillColour(f) {
  f = clamp(+f || 0, 0, 1);
  for (let i = 1; i < FILL_RAMP.length; i++) {
    const [p1, c1] = FILL_RAMP[i];
    const [p0, c0] = FILL_RAMP[i - 1];
    if (f <= p1) {
      const u = (f - p0) / (p1 - p0);
      const c = c0.map((v, k) => Math.round(v + (c1[k] - v) * u));
      return `rgb(${c})`;
    }
  }
  return "rgb(197,48,48)";
}
function pathOf(pts) {
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 1; i < pts.length; i++) d += `L${pts[i][0]},${pts[i][1]}`;
  return d;
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function mmss(t) {
  t = Math.max(0, Math.floor(t));
  return `T+${String(Math.floor(t / 60)).padStart(3, "0")}:${String(t % 60).padStart(2, "0")}`;
}
function toast(text, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = text;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 3300);
}

/* ---------------------------------------------------------------- socket */

function connect() {
  clearTimeout(state.retry);
  setLink("connecting");
  let ws;
  try {
    ws = new WebSocket(WS_URL);
  } catch {
    return retry();
  }
  state.ws = ws;
  ws.onopen = () => {
    state.connected = true;
    state.everConnected = true;
    setLink("live");
    ws.send(JSON.stringify({ type: "hello", ui: "map" }));
    sendControl(state.controls, "sync");
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "frame" && msg.frame) onFrame(msg.frame);
    else if (msg.type === "hello") onHello(msg);
  };
  ws.onclose = () => {
    state.connected = false;
    setLink("down");
    retry();
  };
  ws.onerror = () => ws.close();
}
function retry() {
  clearTimeout(state.retry);
  state.retry = setTimeout(connect, RECONNECT_MS);
}
function send(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify(obj));
}
function sendControl(patch, action) {
  const msg = { type: "control", ...state.controls, ...patch };
  if (action) msg.action = action;
  send(msg);
}
function onHello(msg) {
  if (msg.controls) Object.assign(state.controls, msg.controls);
  if (Array.isArray(msg.scenarios)) {
    const sel = $("#scenario");
    for (const s of msg.scenarios) {
      if (![...sel.options].some((o) => o.value === s)) sel.add(new Option(s, s));
    }
  }
  paintControls();
}
function setLink(kind) {
  const live = kind === "live";
  $("#link").dataset.s = kind;
  $("#linktxt").textContent = { live: "stream live", connecting: "connecting…", down: "stream down" }[kind];
  $("#wait").classList.toggle("show", !live);
  $("#waittxt").textContent = state.everConnected
    ? "stream lost — reconnecting to " + WS_URL
    : "no stream on " + WS_URL + " — retrying";
  $("#mapbox").classList.toggle("stale", !live);
}

/* ---------------------------------------------------------------- network */

const vel = { x: 0, y: 0, raf: 0 };

async function loadNet() {
  const net = await (await fetch("network.json", { cache: "no-store" })).json();
  state.net = net;
  for (const n of net.nodes) state.nodeById.set(n.id, n);
  buildStatic(net);
}
function scheduleRender() {
  if (vel.raf) return;
  const step = (ts) => {
    if (!vel.last) vel.last = ts;
    const dt = Math.min(64, ts - vel.last) / 16.7;
    vel.last = ts;
    state.view.cx -= vel.x * dt / state.view.k;
    state.view.cy += vel.y * dt / state.view.k;
    vel.x *= 0.86; vel.y *= 0.86;
    render();
    if (Math.abs(vel.x) + Math.abs(vel.y) > 0.15) vel.raf = requestAnimationFrame(step);
    else { vel.raf = 0; vel.last = 0; }
  };
  vel.raf = requestAnimationFrame(step);
}

function toScreen(x, y) {
  const v = state.view;
  return [v.k * (x - v.cx) + v.w / 2, -v.k * (y - v.cy) + v.h / 2];
}
function fitView() {
  const bb = state.net.bb;
  const box = $("#mapbox").getBoundingClientRect();
  const w = box.width || 900, hh = box.height || 700;
  const pad = { l: 44, r: 300, t: 44, b: 150 };  // right/bottom left clear for cards and dock
  const k = Math.min((w - pad.l - pad.r) / (bb.x1 - bb.x0), (hh - pad.t - pad.b) / (bb.y1 - bb.y0));
  state.view = { k, w, h: hh, cx: (bb.x0 + bb.x1) / 2 - (pad.r - pad.l) / 2 / k,
                 cy: (bb.y0 + bb.y1) / 2 + (pad.t - pad.b) / 2 / k };
}
function lanePts(l) {
  return l.s.map(([x, y]) => toScreen(x, y));
}

function buildStatic(net) {
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  for (const n of net.nodes) {
    for (const [x, y] of n.p) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  }
  net.bb = { x0, y0, x1, y1 };
  fitView();
  const v = state.view;
  const blocks = $("#l-blocks");
  blocks.innerHTML = `<rect class="blk" x="0" y="0" width="${v.w}" height="${v.h}"/>` +
    `<rect class="basin" x="${-v.k * 140 + v.w / 2}" y="${v.k * 140 + v.h / 2}" width="${v.w}" height="${v.h}"/>`;

  const casing = $("#l-casing"), mark = $("#l-mark"), turnG = $("#l-constructor");
  for (const e of net.edges) {
    const pts = lanePts(e.l[0]);
    h("path", { class: "casing", d: pathOf(pts), "stroke-width": Math.max(6, e.l.length * 3.2 * v.k) }, casing);
  }
  for (const t of net.turns) {
    h("path", { class: "turn " + t.k, d: pathOf(t.s.map(([x, y]) => toScreen(x, y))), "stroke-width": Math.max(0.9, 2.2 * v.k) }, turnG);
  }
  for (const e of net.edges) {
    for (let i = 0; i < e.l.length; i++) {
      if (i > 0) h("path", { class: "mark", d: pathOf(lanePts(e.l[i])) }, mark);
    }
  }

  const boxes = $("#l-boxes"), sigG = $("#l-signals"), hits = $("#l-hits"), veh = $("#l-vehicles");
  const signalIds = new Set(net.nodes.filter((n) => n.g).map((n) => n.id));
  for (const n of net.nodes) {
    const g = h("g", { class: "n" + (n.g ? " inters" : ""), "data-node": n.id }, null);
    const c = toScreen(n.x, n.y);
    const r = Math.max(9, 14 * v.k);
    h("circle", { cx: c[0], cy: c[1], r: r + 7, class: "halo" }, g);
    h("polygon", { class: "jbox", points: n.p.map(([x, y]) => toScreen(x, y).join(",")).join(" ") }, g);
    if (n.g) {
      h("text", { class: "lbl-halo", x: c[0] + r + 5, y: c[1] + r + 11 }, g).textContent = n.id;
      h("text", { class: "lbl major", x: c[0] + r + 5, y: c[1] + r + 11 }, g).textContent = n.id;
      // signal bar on the south face: 3 lamps, lit by phase kind
      const sw = Math.max(10, 22 * v.k);
      h("line", { class: "sig", x1: c[0] - sw / 2, y1: c[1] + r + 3, x2: c[0] + sw / 2, y2: c[1] + r + 3 }, sigG).dataset.node = n.id;
    }
    boxes.appendChild(g);
    if (signalIds.has(n.id)) {
      const hit = h("rect", { class: "hit", x: c[0] - r - 4, y: c[1] - r - 4, width: 2 * (r + 4), height: 2 * (r + 4), "data-node": n.id,
                              tabindex: "0", role: "button", "aria-label": `junction ${n.id}` }, hits);
      hit.dataset.node = n.id;
    }
  }
  for (const n of net.nodes.filter((x) => !x.g)) {
    h("text", { class: "lbl", x: n.x, y: -n.y }, null);
  }
  // placeholder vehicle group ready for first frame
  h("g", { id: "veh-g" }, veh);
}

/* ---------------------------------------------------------------- frames */

function onFrame(f) {
  state.last = f;
  const t = +f.t || 0;
  $("#clock").textContent = mmss(t);
  const nodes = Array.isArray(f.nodes) ? f.nodes : [];
  const byId = new Map(nodes.map((n) => [n.id, n]));

  drawNodes(nodes, byId);
  drawVehicles(f.vehicles || []);
  drawEdges(f.edges || []);
  drawDistrict(f.district, byId);
  drawIntents(f.intents || [], t);
  drawStats(f.stats || {}, t);
  drawOrch(f, byId);
  placeOpenCards(byId);
}

function drawNodes(nodes, byId) {
  if (!state.nodeEls) {
    state.nodeEls = new Map();
    for (const g of $$("#l-boxes g.n[data-node]")) state.nodeEls.set(g.dataset.node, g);
  }
  for (const [id, g] of state.nodeEls) {
    const n = byId.get(id);
    g.classList.remove("press-0", "press-1", "press-2", "press-3", "spill-1", "spill-2", "locked", "stalled");
    if (!n) continue;
    const p = +n.pressure || 0;
    const press = p >= 40 ? 3 : p >= 20 ? 2 : p >= 8 ? 1 : 0;
    g.classList.add("press-" + press);
    if (+n.spill >= 1) g.classList.add("spill-" + clamp(+n.spill | 0, 1, 2));
    if (n.locked) g.classList.add("locked");
    if (n.stalled) g.classList.add("stalled");
  }
  for (const bar of $$("#l-signals .sig")) {
    const n = byId.get(bar.dataset.node);
    bar.setAttribute("class", "sig");
    if (n) bar.classList.add(n.green > 0 ? "on-g" : n.phase >= 0 && n.green === 0 && (n.yellow || n.phase_y) ? "on-y" : "on-r");
  }
}

function ensurePool(parent, cls_, n, order) {
  while (parent.children.length < n) h(order === "rect" ? "rect" : "path", { class: cls_ }, parent);
  while (parent.children.length > n) parent.lastElementChild.remove();
}

function drawVehicles(veh) {
  const g = $("#veh-g");
  if (!g) return;
  const count = Math.min(veh.length, 1600);
  ensurePool(g, "veh", count, "rect");
  const v = state.view;
  const w = clamp(1.9 * v.k, 1.4, 4), l = w * 2.2;
  for (let i = 0; i < count; i++) {
    const p = veh[i];
    const r = g.children[i];
    const [x, y] = toScreen(+p.x || 0, +p.y || 0);
    attr(r, "x", (x - w / 2).toFixed(1));
    attr(r, "y", (y - l / 2).toFixed(1));
    attr(r, "width", w.toFixed(1));
    attr(r, "height", l.toFixed(1));
    attr(r, "transform", `rotate(${(-(+p.a || 0)).toFixed(0)} ${x.toFixed(1)} ${y.toFixed(1)})`);
  }
}

function drawEdges(edges) {
  const fills = $("#l-fill");
  if (!state.edgeEls) {
    state.edgeEls = [];
    for (const e of state.net.edges) {
      const pts = lanePts(e.l[0]);
      state.edgeEls.push(h("path", { class: "edge", d: pathOf(pts),
        "stroke-width": Math.max(1.2, e.l.length * 2.2 * state.view.k) }, fills));
    }
  }
  const byId = new Map(edges.map((e) => [e.id, e]));
  state.net.edges.forEach((e, i) => {
    const f = byId.get(e.id);
    state.edgeEls[i].style.stroke = f ? fillColour(f.fill) : "var(--edge)";
  });
}

function drawDistrict(d, byId) {
  const g = $("#l-cordon");
  g.innerHTML = "";
  const key = d ? `${d.id}:${d.nodes?.join(",")}` : "";
  if (state.cordonKey !== key) {
    state.cordonKey = key;
    if (d) {
      const pts = (d.cycle || d.nodes || []).map((id) => state.nodeById.get(id)).filter(Boolean);
      if (pts.length > 1) {
        const r = Math.max(26, 42 * state.view.k);
        let cx = 0, cy = 0;
        for (const n of pts) { const c = toScreen(n.x, n.y); cx += c[0]; cy += c[1]; }
        cx /= pts.length; cy /= pts.length;
        const dpath = pts.map((n, i) => {
          const c = toScreen(n.x, n.y);
          const dx = c[0] - cx, dy = c[1] - cy, m = Math.hypot(dx, dy) || 1;
          const ox = cx + (dx / m) * r, oy = cy + (dy / m) * r;
          return `${i ? "L" : "M"}${ox},${oy}`;
        }).join(" ") + " Z";
        h("path", { class: "cordon", d: dpath }, g);
        const ring = pts.map((n) => toScreen(n.x, n.y));
        const inner = ring.map(([x, y]) => `${x + (cx - x) * 0.12},${y + (cy - y) * 0.12}`);
        h("path", { class: "cycle-a", d: ring.map(([x, y], i) => `${i ? "L" : "M"}${inner[i]} L${x},${y}`).join(" "), "marker-end": "url(#arrowhead)" }, g);
        for (let i = 0; i < ring.length; i++) {
          const a = ring[i], b = ring[(i + 1) % ring.length];
          const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
          const k = 1 + Math.hypot(mx - cx, my - cy) / (r * 1.6);
          h("path", { class: "cycle-a", d: `M${mx},${my} L${cx + (mx - cx) * k},${cy + (my - cy) * k}`, "marker-end": "url(#arrowhead)" }, g);
        }
      }
    }
  }
  // dim everything outside the district while the orchestrator holds it
  if (state.distKey !== key) {
    state.distKey = key;
    const inD = new Set(d?.nodes || []);
    for (const [id, g2] of state.nodeEls || []) g2.classList.toggle("out", !!d && !inD.has(id));
  }
  if (d) {
    $("#cnt-orch").textContent = "LOCKED";
  } else $("#cnt-orch").textContent = "";
}

function intentOf(n, intents) {
  const list = intents.filter((i) => i.node === n.id);
  return list[list.length - 1] || null;
}
function applyKind(i) {
  if (!i || !i.applied) return "idle";
  if (i.applied === "ok") return "ok";
  if (i.applied === "stall") return "stall";
  if (i.applied.startsWith("façade:") || i.applied.startsWith("facade:")) return "facade";
  return i.applied;
}

/* ---------------------------------------------------------------- intents feed */

function drawIntents(intents, t) {
  for (const i of intents) {
    state.feed.unshift({ ...i, t });
  }
  state.feed.length = Math.min(state.feed.length, 300);
  $("#cnt-int").textContent = intents.length || "";
  renderFeed();
}

function renderFeed() {
  const onlyBad = $("#only-nonok").checked;
  const rows = state.feed.filter((r) => !onlyBad || applyKind(r) !== "ok").slice(0, INTENT_BLOCKS);
  const ol = $("#feed");
  if (!rows.length) {
    ol.innerHTML = `<li class="empty">${state.feed.length ? "no failures in buffer" : "no intents yet"}</li>`;
    return;
  }
  const order = rows.map((r) => `${r.node}|${r.t}|${r.why}|${r.applied}`).join("~");
  if (ol.dataset.order === order) return;
  ol.dataset.order = order;
  ol.innerHTML = rows.map((r) => {
    const k = applyKind(r);
    const nm = state.nodeById.get(r.node);
    return `<li class="st-${k}" data-node="${esc(r.node)}" title="${esc(nm?.name || r.node)}">
      <span class="f-id">${esc(r.node)}</span>
      <span><span class="stripe"></span><span class="f-act">${esc(r.action)}${r.target >= 0 ? " p" + esc(r.target) : ""} · ${esc(r.green)}s</span>
        <span class="f-why"> — ${esc((r.why || "").slice(0, 140))}</span></span>
      <span class="f-meta"><span class="badge ${k}">${k === "ok" ? "ok" : esc(r.applied)}</span><span class="badge">${(+r.latency || 0).toFixed(1)}s</span></span>
    </li>`;
  }).join("");
}

/* ---------------------------------------------------------------- metrics */

const TILE = [
  ["avg wait", "wait", "s", (v) => v >= 60 ? "hot" : v >= 30 ? "warn" : ""],
  ["avg speed", "speed", "m/s", (v) => v < 2 ? "hot" : v < 4 ? "warn" : ""],
  ["throughput", "throughput", "veh/h", () => ""],
  ["vehicles", "veh", "", () => ""],
  ["blocked arcs", "blocked", "", (v) => v > 0 ? "hot" : ""],
  ["in district", "in_district", "", () => ""],
];
function drawStats(s, t) {
  state.statsHist.push({ t, blocked: +s.blocked || 0, wait: +s.wait || 0, speed: +s.speed || 0 });
  if (state.statsHist.length > 240) state.statsHist.shift();
  if (!state.open.has("metrics")) return;
  renderMetrics(s);
}
function renderMetrics(s) {
  const box = $("#metrics");
  const tiles = TILE.map(([label, key, unit, kind]) => {
    const v = s[key] ?? 0;
    const val = key === "wait" || key === "speed" ? (+v).toFixed(1) : Math.round(+v);
    return `<div class="tile ${kind(+v)} ${key === "blocked" ? "always" : ""}"><dt>${label}</dt><dd>${val}<small>${unit ? " " + unit : ""}</small></dd></div>`;
  });
  const spark = (data, key, cls2) => {
    if (data.length < 2) return "";
    const max = Math.max(1, ...data.map((d) => d[key]));
    const pts = data.map((d, i) => `${(i / (data.length - 1)) * 100},${46 - (d[key] / max) * 42}`).join(" ");
    return `<svg class="spark" viewBox="0 0 100 46" preserveAspectRatio="none"><path class="${cls2}" d="M${pts.replace(/ /g, " L")}" vector-effect="non-scaling-stroke"/></svg>`;
  };
  box.innerHTML = `<dl class="tiles">${tiles.join("")}</dl>
    <div class="spark-row">
      <figure><figcaption>blocked arcs</figcaption>${spark(state.statsHist, "blocked", "blocked")}</figure>
      <figure><figcaption>avg wait (s)</figcaption>${spark(state.statsHist, "wait", "")}</figure>
    </div>`;
  $("#cnt-met").textContent = Math.round(+s.blocked || 0) || "";
}

/* ---------------------------------------------------------------- orchestrator */

function drawOrch(f, byId) {
  const d = f.district;
  const prev = state.orch.state;
  let st = "idle";
  if (d) st = "lock";
  else if ((+f.stats?.blocked || 0) >= 6) st = "detect";
  const changed = st !== prev;
  state.orch.state = st;
  const id = d ? `${d.id}:${d.gen}:${d.nodes.join(",")}` : "";
  if (changed) {
    if (st === "lock") toast(`ORCHESTRATOR LOCKED district ${d.id} · gen ${d.gen}`, "warn");
    if (prev === "lock" && st !== "lock") toast("district handed back to node agents");
  }
  if (st === "lock" && id !== state.orch.id) {
    const nodes = d.nodes.map((n) => `<span class="pill node">${esc(n)}</span>`).join("");
    const cyc = (d.cycle || []).map((n) => `<span class="pill node">${esc(n)}</span>`).join(" → ");
    $("#orch").dataset.state = "lock";
    $("#orch-state").textContent = `holding district ${d.id} · gen ${d.gen} · ${d.nodes.length} nodes`;
    $("#orch-body").innerHTML = `
      <div class="row"><span class="k">locked</span>${nodes}</div>
      <div class="row"><span class="k">blocked cycle</span>${cyc || "<span class='pill'>—</span>"}</div>
      <div class="row"><span class="k">escape</span><span class="pill">inserting escape phase at the most blocked downstream</span></div>
      <div class="row"><span class="k">hand-back gate</span><span class="pill">spill ≤ 1 · drain objective · 2 clean ticks</span></div>`;
    state.orch.id = id;
  } else if (st === "detect" && changed) {
    $("#orch").dataset.state = "detect";
    $("#orch-state").textContent = "cycle detected — orchestrator waking";
    $("#orch-body").innerHTML = `<div class="row"><span class="k">blocked arcs</span><span class="pill">${Math.round(f.stats.blocked)} at fill ≥ 0.9</span></div>`;
  } else if (st === "idle" && changed) {
    $("#orch").dataset.state = "idle";
    $("#orch-state").textContent = "idle — no blocked cycle detected";
    $("#orch-body").innerHTML = `<div class="row">${(f.stats?.blocked ?? 0) ? "blocked arcs below persistence threshold" : "grid free-flowing"}</div>`;
    state.orch.id = "";
  }
}

/* ---------------------------------------------------------------- thought cards */

function cardHTML(n, pin) {
  const i = intentOf(n, state.last?.intents || []);
  const k = applyKind(i);
  const nm = state.nodeById.get(n.id)?.name || n.id;
  const badges = [
    `<span class="badge ${n.locked ? "locked" : ""}">${n.locked ? "orchestrator lock" : "local agent"}</span>`,
    n.stalled ? `<span class="badge stall">stalled</span>` : "",
    n.spill >= 2 ? `<span class="badge parked">spillback FULL</span>` : n.spill >= 1 ? `<span class="badge stall">spillback ${n.spill}</span>` : "",
  ].filter(Boolean).join("");
  const app = i ? `<span class="badge ${k}">${k === "idle" ? "staged" : esc(i.applied)}</span>` : `<span class="badge idle">no intent</span>`;
  const why = i ? esc(i.why || "—") : "no decision in this tick";
  const q = (n.queues || []).reduce((a, b) => a + (+b || 0), 0);
  const phases = Array.from({ length: 6 }, (_, x) =>
    `<i class="${x === +n.phase ? "cur" : ""} ${i && x === +i.target ? "tgt" : ""}"></i>`).join("");
  return `<div class="card-h"><span class="card-id">${esc(n.id)}</span><span class="card-name">${esc(nm)}</span>
      ${pin ? `<button class="x" data-dismiss title="dismiss (esc)">×</button>` : ""}</div>
    <div class="chips">${badges}${app}</div>
    <p class="why ${i ? "" : "none"}">${why}</p>
    <div class="phase">${phases}</div>
    <dl class="kv">
      <div><dt>pressure</dt><dd class="${n.pressure >= 20 ? "hot" : ""}">${Math.round(n.pressure)}</dd></div>
      <div><dt>queue</dt><dd class="${q >= 20 ? "warn" : ""}">${q}</dd></div>
      <div><dt>green</dt><dd>${n.green}s</dd></div>
      <div><dt>latency</dt><dd>${i ? (+i.latency).toFixed(1) + "s" : "—"}</dd></div>
    </dl>`;
}

function ensureCard(id, pin) {
  let el = state.cards?.[id];
  if (!el) {
    el = document.createElement("div");
    el.dataset.node = id;
    $("#cards").appendChild(el);
  }
  el.className = "card" + (pin ? " pinned" : "");
  const n = (state.last?.nodes || []).find((x) => x.id === id) || { id };
  el.innerHTML = el.innerHTML ? cardHTML(n, pin) : "";
  if (!el.innerHTML) el.innerHTML = cardHTML(n, pin);
  placeCard(el, id, pin);
  return el;
}

function placeCard(el, id, foot) {
  const n = state.nodeById.get(id);
  if (!n || !state.view) return;
  const [x, y] = toScreen(n.x, n.y);
  const half = el.offsetWidth / 2 || 144;
  const v = state.view;
  el.style.left = clamp(x, half + 8, v.w - half - 8) + "px";
  el.style.top = y + "px";
  el.classList.toggle("foot", foot && y < 170);
}
function placeOpenCards(byId) {
  for (const [id, el] of Object.entries(state.cards || {})) {
    if (!el || !el.parentNode) continue;  // dropped by a view rebuild
    if (!byId.has(id)) { el.remove(); delete state.cards[id]; continue; }
    if (id === state.pinId) el.innerHTML = cardHTML(byId.get(id), true);
    else if (id === state.hoverId) el.innerHTML = cardHTML(byId.get(id), false);
    if (el.innerHTML) placeCard(el, id, id === state.pinId);
  }
}
state.cards = {};

function pin(id) {
  if (state.pinId && state.pinId !== id) unpin(state.pinId, false);
  state.pinId = id;
  const el = ensureCard(id, true);
  state.nodeEls?.get(id)?.classList.add("sel");
}
function unpin(id = state.pinId, clearHover = true) {
  if (!id) return;
  state.cards?.[id]?.remove();
  delete state.cards?.[id];
  state.nodeEls?.get(id)?.classList.remove("sel");
  if (state.pinId === id) state.pinId = null;
  if (clearHover && state.hoverId === id) state.hoverId = null;
}

/* ---------------------------------------------------------------- hover */

const tip = $("#tip");
function showTip(id, ev) {
  const n = state.last?.nodes?.find((x) => x.id === id);
  const nm = state.nodeById.get(id)?.name || id;
  if (!n) { showNodeTip(id, ev, nm); return; }
  const i = intentOf(n, state.last.intents || []);
  tip.hidden = false;
  tip.innerHTML = `<b>${esc(id)}</b><span class="t-name">${esc(nm)}</span>
    <span class="t-line">${i ? `${esc(i.action)}${i.target >= 0 ? " p" + esc(i.target) : ""} · ${esc(i.green)}s — ${esc((i.why || "—").slice(0, 120))}` : "no intent this tick"}</span>
    <span class="t-meta">pressure ${Math.round(n.pressure)} · queue ${(n.queues || []).reduce((a, b) => a + (+b || 0), 0)} · spill ${n.spill} · ${i ? esc(i.applied || "staged") : "—"} · ${i ? (+i.latency).toFixed(1) + "s" : ""}</span>
    <span class="t-hint">click to pin</span>`;
  moveTip(ev);
}
function showNodeTip(id, ev, nm) {
  tip.hidden = false;
  tip.innerHTML = `<b>${esc(id)}</b><span class="t-name">${esc(nm)}</span><span class="t-line">not in this frame</span>`;
  moveTip(ev);
}
function moveTip(ev) {
  if (tip.hidden) return;
  const pad = 14;
  const w = tip.offsetWidth, hh = tip.offsetHeight;
  tip.style.left = clamp(ev.clientX + pad, 6, innerWidth - w - 6) + "px";
  tip.style.top = clamp(ev.clientY + pad, 64, innerHeight - hh - 6) + "px";
}
function hideTip() { tip.hidden = true; }

$("#l-hits").addEventListener("pointerover", (e) => {
  const hit = e.target.closest(".hit");
  if (!hit) return;
  const id = hit.dataset.node;
  if (state.hoverId === id) return;
  if (state.hoverId && state.hoverId !== state.pinId) {
    state.cards?.[state.hoverId]?.remove();
    delete state.cards?.[state.hoverId];
    hideTip();
  }
  state.hoverId = id;
  if (id === state.pinId) { showTip(id, e); return; }  // pinned card already explains it
  const el = ensureCard(id, false);
  el.dataset.hover = "1";
  showTip(id, e);
});
$("#l-hits").addEventListener("pointermove", (e) => { if (tip.hidden) return; moveTip(e); });
$("#l-hits").addEventListener("pointerout", (e) => {
  const hit = e.target.closest(".hit");
  if (!hit || hit.contains(e.relatedTarget)) return;
  const id = hit.dataset.node;
  hideTip();
  if (id === state.pinId) { if (state.hoverId === id) state.hoverId = null; return; }
  state.cards?.[id]?.remove();
  delete state.cards?.[id];
  if (state.hoverId === id) state.hoverId = null;
});
$("#l-hits").addEventListener("click", (e) => {
  const hit = e.target.closest(".hit");
  if (!hit || state.dragMoved) return;
  const id = hit.dataset.node;
  if (state.pinId === id) { unpin(id, false); return; }
  pin(id);
});
$("#l-hits").addEventListener("keydown", (e) => {
  const hit = e.target.closest(".hit");
  if (!hit) return;
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pin(hit.dataset.node); }
});
$("#cards").addEventListener("click", (e) => {
  const x = e.target.closest("[data-dismiss]");
  if (x) { e.stopPropagation(); const el = x.closest(".card"); state.nodeEls?.get(el.dataset.node)?.classList.remove("sel"); state.pinId = null; el.remove(); delete state.cards[el.dataset.node]; }
});

/* ---------------------------------------------------------------- tabs */

function setTab(name, on) {
  const tab = $(`#tab-${name}`);
  if (!tab) return;
  tab.classList.toggle("open", on);
  $(".chip", tab).setAttribute("aria-expanded", String(on));
  on ? state.open.add(name) : state.open.delete(name);
  if (on && name === "metrics" && state.last) renderMetrics(state.last.stats || {});
}
$$("#tabs .chip").forEach((chip) => chip.addEventListener("click", () => {
  const tab = chip.closest(".tab");
  setTab(tab.dataset.tab, !tab.classList.contains("open"));
}));
$$("#tabs [data-close]").forEach((x) => x.addEventListener("click", () => {
  setTab(x.closest(".tab").dataset.tab, false);
  $(".chip", x.closest(".tab")).focus();
}));
$("#feed").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-node]");
  if (li) pin(li.dataset.node);
});
$("#only-nonok").addEventListener("change", () => { $("#feed").dataset.order = ""; renderFeed(); });

/* ---------------------------------------------------------------- controls */

function paintControls() {
  const c = state.controls;
  $("#btn-pause").textContent = c.paused ? "▶" : "❚❚";
  $("#btn-pause").classList.toggle("paused", !!c.paused);
  $("#btn-pause").setAttribute("aria-pressed", String(!!c.paused));
  $$("#speed button").forEach((b) => b.classList.toggle("on", +b.dataset.s === +c.speed));
  const l = $("#btn-llm");
  l.classList.toggle("on", !!c.llm);
  l.textContent = c.llm ? "LLM on" : "LLM off";
  $("#scenario").value = c.scenario;
}
function setControl(patch, action, quiet) {
  Object.assign(state.controls, patch);
  paintControls();
  if (!quiet) sendControl(patch, action);
}
$("#btn-pause").addEventListener("click", () => setControl({ paused: !state.controls.paused }, "pause"));
$("#speed").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-s]");
  if (b) setControl({ speed: +b.dataset.s }, "speed");
});
$("#btn-llm").addEventListener("click", () => {
  const on = !state.controls.llm;
  setControl({ llm: on }, "llm");
  toast(on ? "LLM agents on" : "LLM agents off — façade holds last phase", on ? "" : "warn");
});
$("#scenario").addEventListener("change", (e) => {
  setControl({ scenario: e.target.value }, "scenario");
  toast(`scenario → ${e.target.value}`, "warn");
});
function restart() {
  setControl({ paused: false }, "restart");
  state.statsHist = [];
  state.feed = [];
  unpin();
  toast("episode restarted");
  const m = $("#map");
  m.classList.remove("pop"); void m.offsetWidth; m.classList.add("pop");
}
$("#btn-restart").addEventListener("click", restart);

addEventListener("keydown", (e) => {
  if (e.target.matches("input, select")) return;
  if (e.key === " ") { e.preventDefault(); setControl({ paused: !state.controls.paused }, "pause"); }
  else if (e.key === "r" || e.key === "R") restart();
  else if (e.key === "Escape") { if (state.pinId) unpin(); else if (state.open.size) state.open.forEach((n) => setTab(n, false)); }
  else if (/^[1-4]$/.test(e.key)) setControl({ speed: [1, 2, 4, 8][+e.key - 1] }, "speed");
  else if (e.key === "l" || e.key === "L") $("#btn-llm").click();
});

/* ---------------------------------------------------------------- pan / zoom */

let drag = null;
$("#map").addEventListener("pointerdown", (e) => {
  if (e.target.closest(".hit")) return;
  drag = { x: e.clientX, y: e.clientY };
  state.dragMoved = false;
  $("#map").classList.add("drag");
  $("#map").setPointerCapture(e.pointerId);
  vel.x = vel.y = 0;
});
$("#map").addEventListener("pointermove", (e) => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) state.dragMoved = true;
  drag = { x: e.clientX, y: e.clientY };
  vel.x = dx; vel.y = dy;
  scheduleRender();
});
$("#map").addEventListener("pointerup", () => { drag = null; $("#map").classList.remove("drag"); setTimeout(() => (state.dragMoved = false), 0); });
$("#map").addEventListener("wheel", (e) => {
  e.preventDefault();
  const v = state.view;
  const before = [(e.offsetX - v.w / 2) / v.k + v.cx, -(e.offsetY - v.h / 2) / v.k + v.cy];
  v.k = clamp(v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12), 0.4, 4);
  v.cx = before[0] - (e.offsetX - v.w / 2) / v.k;
  v.cy = before[1] + (e.offsetY - v.h / 2) / v.k;
  render();
}, { passive: false });
addEventListener("dblclick", () => { fitView(); scheduleRender(); });

/* Rebuild geometry when the view moves. */
function render() {
  if (!state.net) return;
  for (const el of $$(".card")) el.remove();
  $("#view").innerHTML = "";
  buildStatic(state.net);
  if (state.last) onFrame(state.last);
}
addEventListener("resize", () => { if (state.net) fitView(); render(); });

/* ---------------------------------------------------------------- boot */

paintControls();
$("#only-nonok").checked = false;
for (const t of new URLSearchParams(location.search).getAll("tab")) setTab(t, true);
loadNet().then(() => {
  setLink("connecting");
  connect();
});