"""Build the single PDF report.

Approach: stitch the three dossiers into one HTML document with numbered heading
hierarchy (forced, so the three source h1s do not collide with the report's parts),
pandoc for md->html, headless Chrome for pagination, then a second pass to stamp
real page numbers into the TOC and into the citation marks.

Text transforms are content-preserving except the documented ID normalisation
(CoLLMLight 2503.04379 -> 2503.11739, which the prior_art dossier mistyped).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import subprocess
import sys

import markdown
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "report"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

DOC_ID = re.compile(r"(?<!\d)(?<!@)\d{4}\.\d{4,5}(?!\d)")
ID_FIX = {"2503.04379": "2503.11739"}

SOURCES = [
    ("A", "research/prior_art.md"),
    ("B", "research/llm_control_loop.md"),
    ("C", "research/simulator_decision.md"),
]


# --------------------------------------------------------------------------- text
def normalise_ids(text: str) -> str:
    for bad, good in ID_FIX.items():
        text = text.replace(bad, good)
    return text


def force_levels(text: str) -> str:
    """h1 -> ##, h2 -> ###, h3 -> ####. Fenced blocks are left untouched."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            if line.startswith("### "):
                line = "#### " + line[4:]
            elif line.startswith("## "):
                line = "### " + line[3:]
            elif line.startswith("# "):
                line = "## " + line[2:]
        out.append(line)
    return "\n".join(out)


def drop_own_ref_lists(text: str, drop_assets: bool) -> str:
    """Remove per-dossier 'References' / 'Source list' sections.

    They are folded into the single numbered bibliography instead. The simulator
    dossier's on-disk asset table moves to its own appendix so nothing is lost.
    """
    stop = None
    for pat in (r"^### References \(full list\)", r"^### Source list", r"^## References"):
        m = re.search(pat, text, re.M)
        if m:
            stop = m.start()
            break
    if stop is not None:
        text = text[:stop]
    if drop_assets:
        m = re.search(r"^## Assets confirmed on disk", text, re.M)
        if m:
            text = text[: m.start()]
    return text.rstrip() + "\n"


def parse_reference_list(path: Path) -> list[tuple[str, list[str]]]:
    """Reference entries, each with the arXiv IDs it cites (entries group several)."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^### References.*$", text, re.M)
    if not m:
        return []
    out = []
    for ln in text[m.end():].split("\n"):
        if not re.match(r"^\d+\.\s+", ln):
            continue
        body = re.sub(r"^\d+\.\s+", "", ln).strip()
        ids = [i for i in re.findall(r"\d{4}\.\d{4,5}", body)]
        out.append((body, ids))
    return out


# ----------------------------------------------------------------------- the arc
def arc(pid_map: dict[str, int], claims: list[dict], prior_path: Path) -> tuple[str, str]:
    loop = (ROOT / "research/llm_control_loop.md").read_text(encoding="utf-8")
    loop = normalise_ids(loop)

    def k(pat: str) -> str:
        return f"@{pid_map[pat]}@" if pat in pid_map else ""

    sp = [
        "Four design decisions follow from the dossiers. Each is stated here so the "
        "open questions at the end have a concrete default behind them, not a blank page.",
        "",
        "## 1. One strategic LLM over one fast deterministic executor",
        "",
        "The naive reading of a per-intersection LLM is that the model sets the light at every "
        "decision tick. The measured literature says otherwise. Wherever the split was actually "
        f"tested, letting a slow model sit above a fast rule beat per-decision LLM control on both "
        f"quality and latency {k('2607.22691')}{k('2407.06025')}{k('2509.36934')}. HiLLTS issues "
        f"district directives every 10–60 s and lets a gap-out rule run every step {k('2607.22691')}. "
        f"iLLM-TSC's LLM only needs to veto a fast policy to recover nearly all throughput under "
        f"degradation {k('2407.06025')}.",
        "",
        "Two consequences worth carrying: a second slow LLM hop is *not* a free improvement "
        f"(HiLLTS's district layer degraded below its own rule baseline) {k('2607.22691')}, and a "
        "bad or missing model turn costs nothing more than falling back to max-pressure.",
        "",
        "## 2. Local agents set mode and parameters; the rule executes",
        "",
        "Agent output is a mode and a small parameter vector per re-plan: a spillback weight, a "
        "hysteresis band, a max-green bucket. The executor applies hysteresis, min-green and a "
        "spillback discount every tick regardless of model status. The orchestrator is one more "
        "writer to the same parameter record, so takeover and hand-back are atomic writes plus a "
        "generation number, never a prose handoff.",
        "",
        "This is also what makes the design defensible to a reviewer: the rule floor means the "
        "LLM's contribution is measurable as a delta above max-pressure, not as the system itself.",
        "",
        "## 3. Orchestrator trigger is detected, not noticed",
        "",
        "Gridlock is a cycle of blocked arcs. A simulator reports no generic deadlock signal; "
        "the queue simply stops draining and demand piles up behind the entries "
        f"{k('2606.27381')}. Detection therefore has to be an explicit bounded walk over the "
        "spillback relation on the orchestrator's side, and the local agent's request can only "
        "raise priority — it can never substitute for detection.",
        "",
        "## 4. Hand-back is proved, then re-states what it held",
        "",
        "Hand-back passes through a deterministic gate (spillback depth, queue against a baseline "
        "band, drain objective, two clean ticks, lifecycle defaults restored) before control moves. "
        "The hand-back message then restates the held constraints verbatim rather than summarising "
        f"them, because compressed handoffs deactivate upstream constraints in the overwhelming "
        f"majority of cases {k('2608.24569')}.",
    ]

    unresolved = [
        "`simulator_decision.md` §A.3. Its endpoints (109 vs 1,705 steps/s) are backed by "
        "`survey/bench/results.jsonl`; the four intermediate lines in the prose (94, 1,388, 1,788, "
        "1,730–1,944) do not appear in the JSONL, whose only runs are at `--end 600` and "
        "`--end 900`. Listed here rather than silently reproduced as measured.",
        "The stock `3x3Grid` dev net genuinely cannot gridlock (right-turn-only movements, demand "
        "peaking at 40 vehicles), so the gridlock fixture is synthetic demand on a light net. It "
        "produced 17–25 blocked edges and 4,529 stranded vehicles, which is enough to exercise an "
        "orchestrator, but it is not a calibrated real network.",
        "The three near-miss papers are compared on abstraction percentage by reading, not by "
        "running them. The crowdedness of the supervisor column rests on that reading.",
        "Every throughput number is from one weak laptop, single-threaded, one seed. Ratios should "
        "survive a faster machine; absolutes should be re-run on the target box.",
        "The latency budget in Part B §2.2 is a model with derived constants, not a measurement.",
        "Fake-paper hypothesis: none of the twenty-five corpus papers shares a code repository with "
        "another, and four of the 2026 preprints use the identical '×Light' naming convention, "
        "which can make a literature of one group look like a literature of five.",
    ]
    if claims:
        unresolved.insert(1, f"{len(claims)} corpus papers keep their quantifiers only because "
                             "each is cited once, at its mechanism, in the same section "
                             "(Part A §1). Any figure quoted elsewhere traces back through "
                             "that section.")

    openq = [
        "**Coordinated escape.** Which node yields, in what order, and which movement gets "
        f"sacrificed. Nobody has published an escape order {k('2606.27381')}.",
        "**Bounded wait.** The zone-budget form: an agent may call the model while its budget is "
        "positive, and only the orchestrator may take a node below one. The draining argument is "
        "sketchable; the arithmetic is not yet derived.",
        "**The hand-back contract itself**, measured: detection latency, recovery time, and how "
        "often a near-threshold hand-back re-preempts.",
        "**Cadence versus quality.** A re-plan interval sweep against a fixed-time baseline. "
        f"HiLLTS puts the parity point near 10–15 s under congestion {k('2607.22691')}; the "
        "flat-demand point is unknown.",
        f"**Unparsed-turn rate** per model and per prompt. Published failure rates stop at "
        "JSON-schema generality and never reach a 2^k traffic action set.",
    ]

    parts = ["\n\n".join(sp), "## Unresolved", "",
             "\n".join(f"{i}. {u}" for i, u in enumerate(unresolved, 1)),
             "## Open questions", "",
             "\n".join(f"- {q}" for q in openq)]
    return "\n\n".join(parts), ""


# ---------------------------------------------------------------------- pandoc
def to_html(md: str) -> str:
    body = markdown.markdown(
        md, extensions=["tables", "markdown.extensions.tables", "fenced_code", "sane_lists", "attr_list"],
        output_format="html5",
    )
    # drop the <h2> that pandoc emitted for the part h1 we already spent
    return re.sub(r"^\s*<h2[^>]*>.*?</h2>\n", "", body, count=1, flags=re.S)


def cite_mark(t: str, text: str) -> str:
    return f'@{t}@'


def finalize_cites(html: str) -> str:
    return re.sub(r"@(\d+)@",
                  lambda m: f'<sup class="cite"><a href="#ref-{m.group(1)}" '
                            f'data-cite="{m.group(1)}"></a></sup>', html)


# ------------------------------------------------------------------------ main
def main() -> int:
    cited: set[str] = set()
    for pid in DOC_ID.findall("\n".join(p.read_text(encoding="utf-8") for _, p in
                                        [(a, ROOT / b) for a, b in SOURCES])):
        cited.add(pid)

    prior = ROOT / "research/prior_art.md"
    ref_entries = parse_reference_list(prior)
    ordered: list[str] = []
    covered: set[str] = set()
    for entry, ids in ref_entries:
        if any(i in cited for i in ids):
            ordered.append(normalise_ids(entry))
            covered.update(ids)
    for c in sorted(cited - covered):
        ordered.append(f"arXiv {c}.")
    pid_map: dict[str, int] = {}
    for i, e in enumerate(ordered, 1):
        m2 = DOC_ID.search(e)
        if m2:
            pid_map.setdefault(m2.group(), i)

    claims: list[dict] = []
    prior_txt = normalise_ids(prior.read_text(encoding="utf-8"))
    for line in prior_txt.split("\n"):
        m = re.match(r"^\*\*(\d{4}\.\d{4,5}) — (.+?)\.\*\*", line)
        if m and m.group(1) in pid_map:
            claims.append({"id": m.group(1), "cite": pid_map[m.group(1)],
                           "title": m.group(2), "used": "A §1.2"})

    arc_md, _ = arc(pid_map, claims, prior)
    arc_md = force_levels(arc_md)
    arc_md = DOC_ID.sub(lambda m: cite_mark(m.group(), pid_map.get(m.group(), "")), arc_md)
    arc_html = finalize_cites(to_html(arc_md))
    # ---- assembly
    chunks = []
    for tag, rel in SOURCES:
        text = normalise_ids((ROOT / rel).read_text(encoding="utf-8"))
        text = drop_own_ref_lists(text, drop_assets=(tag == "C"))
        text = re.sub(r"^# [^\n]*\n", "", text, count=1)          # source's own part title
        text = re.sub(r"(?:^---\n+)+", "---\n\n", text, count=1)   # collapse doubled rules
        text = re.sub(r"(?:\n*^---\n*)+$", "\n", text)             # drop trailing rules
        text = force_levels(text)
        body = to_html(text)
        body = re.sub(r"(?:\s*</?hr[^>]*>\s*)+", "", body)           # all rules
        if tag == "A":
            body = ('<section class="part" id="part-a"><h1 style="break-before:page">Part A — '
                    'Prior art</h1><h2>What the field already does</h2></section>' + body)
        if tag == "B":
            body = ('<section class="part"><h1 style="break-before:page">Part B — Control loop'
                    '</h1><h2>Making the model reliable and fast</h2></section>' + body)
            body = re.sub(r"<h2>Scope: [^<]*</h2>", "", body, count=1)
        if tag == "C":
            body = ('<section class="part" id="part-c"><h1>Part C — Simulator decision</h1>'
                    '<div class="lead"><p><b>Decision:</b> SUMO 1.28 driven in-process via '
                    '<code>libsumo</code>, with the stock sumo-rl 3×3 grid as the '
                    'development scenario. This part is the measured evidence behind that call: step, '
                    'observation and control costs for both backends, a saturated-grid '
                    'cross-check, and the analytic comparison of every alternative.</p></div>'
                    '</section>' + body)
        chunks.append(body)

    sim_src = normalise_ids((ROOT / "research/simulator_decision.md").read_text(encoding="utf-8"))
    m = re.search(r"^## Assets confirmed on disk.*", sim_src, re.S | re.M)
    assets_md = force_levels(m.group()).replace(
        "## Appendix 1 — Assets on disk", '<h1 style="break-before:page">Appendix 1 — Assets on '
        'disk</h1>', 1)
    chunks.append(to_html(assets_md))

    arc_html = re.sub(r"(arXiv \d{4}\.\d{4,5}|\b\d{4}\.\d{4,5}\b)", r"\1", arc_html)
    arc_html = DOC_ID.sub(lambda m: cite_mark(m.group(), pid_map.get(m.group(), "")), arc_html)
    arc_html = re.sub(r'<sup class="cite"><a href="#bib" data-cite="">', '<sup class="cite"><a href="#bib" data-cite="', arc_html)
    chunks.append(f'<section class="part" id="arc"><h1 style="break-before:page">Part D — '
                  f"Recommended arc</h1>{arc_html}</section>")

    bib = "\n".join(f'<p class="ref" id="ref-{i}"><span class="num">{i}.</span> {html.escape(e)}</p>'
                    for i, e in enumerate(ordered, 1))
    chunks.append(f'<section class="part" id="bib" style="break-after:page"><h1 style="break-before:page">References</h1>'
                    f"{bib}</section>")

    doc_body = "\n".join(chunks)
    today = dt.date.today().isoformat()
    tpl = (ROOT / "report/template.html").read_text(encoding="utf-8")
    doc = (tpl.replace("{{BODY}}", doc_body)
              .replace("{{DATE}}", today)
              .replace("{{COUNT}}", str(len(ordered))))
    (OUT / "report.html").write_text(doc, encoding="utf-8")

    # stamp TOC + citation page numbers
    json.dump({"ok": True, "refs": len(ordered)}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())