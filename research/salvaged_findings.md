# Salvaged findings from the killed research pass (2026-10-29)

Raw material recovered from interrupted subagent transcripts. Not yet verified line-by-line.

## Environment facts (machine-checked by subagent, task 1)
- SUMO 1.27.0 ALREADY INSTALLED at `C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe` (GUI+Proj+SWIG builds).
- winget has `EclipseFoundation.SUMO` 1.28.0 available (upgrade path).
- pip wheels `eclipse-sumo`, `traci`, `libsumo` all install cleanly on Windows py3.12 (eclipse-sumo wheel is 158 MB).
- `cityflow` has NO PyPI distribution at all — source build only; upstream install doc pushes Docker.
- Flow framework: last push July 2024, not archived but effectively unmaintained.
- Laptop GPU confirmed RTX 3050 Laptop 4 GiB.

## Literature hits (titles/IDs, abstracts not yet fully digested)
- CoLLMLight — arXiv 2503.11739 — cooperative LLM agents for network-wide TSC; structured spatiotemporal graph; explicit "complexity-aware reasoning" that switches between no-coop / simple / complex reasoning strategies (an efficiency lever worth stealing).
- Traffic-R1 — arXiv 2508.02344 — RL/reward-finetuned LLM for TSC, human-like reasoning.
- VLMLight — arXiv 2505.19486 — vision-language meta-control, safety-critical.
- arXiv 2601.15816 — LLM-augmented TSC; explicitly criticises raw LLM control on latency grounds.
- arXiv 2510.26242 — LLM TSC + hallucination in emergencies.
- arXiv 2607.22691 — LLM TSC paper with urban-topology angle; "formula" hits suggest real math.
- LibSignal — arXiv 2211.10649 — cross-simulator TSC library.
- PressLight — KDD 2019 — learning max-pressure; pressure defined per phase as in/out queue difference.
- arXiv 1903.04527 — MA2C, multi-agent DRL large-scale TSC.
- RESCO — NeurIPS Datasets & Benchmarks 2021 — RL benchmark for TSC (note: it is a benchmark, also usable by non-RL methods).
- LibSignal / CoLight / PressLight all ship code.

## Hardware / serving reality (task 3, measured)
KV cache per token, fp16, measured arithmetic from real config.json layer counts:
- Qwen2.5-1.5B: 28 KiB/token -> 2 GiB holds ~75k tokens
- Qwen2.5-3B: 36 KiB/token -> 2 GiB holds ~58k tokens
- Llama-3.2-3B: 112 KiB/token -> 2 GiB holds ~19k tokens
- Phi-3.5-mini: 384 KiB/token -> 2 GiB holds ~5k tokens (GQA-poor, worst choice)
- T4 is sm75: no Marlin kernels, no FP8; 7B AWQ land at ~15-30 tok/s. fp16 7B (14 GiB) does not fit.

Structured output options identified: vLLM structured_outputs, instructor (validation+retry), Outlines (FSM-constrained generation), lm-format-enforcer.