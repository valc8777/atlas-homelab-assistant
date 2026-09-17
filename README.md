# ATLAS

A self-hosted, privacy-first AI assistant built from scratch to run entirely on local hardware — local LLM inference, local memory, local evaluation, no cloud dependency for the assistant's core reasoning. ~45,000 lines of Python, built and hardened over several months of continuous, evidence-driven iteration.

This repo is a curated snapshot of the working codebase. Real hostnames, IP addresses, and personal data have been replaced with placeholders; the engineering — architecture, defect-hunting methodology, and evaluation discipline — is real and unedited.

## What it does

ATLAS is a conversational assistant reachable from a phone (installable PWA), Apple Shortcuts, or any HTTP client. It answers general-knowledge questions, checks weather, does unit/date math, manages reminders and notes, recalls and corrects personal preferences, and reads a small allow-listed set of home-automation and homelab-infrastructure state — all while keeping every write action behind an explicit confirmation gate and every "current information" claim behind a live-search requirement rather than static model memory.

## Why it's architecturally interesting

Most "wrap an LLM in a chat UI" projects hand the model total control over routing and let it call tools freely. ATLAS deliberately does not do that for anything safety- or correctness-critical:

- **A model-based planner proposes an intent** (answer directly, retrieve, deep multi-step plan, clarify, or act), but a **deterministic policy layer validates every proposal** before anything executes. An action the model proposes is never trusted at face value — it has to match an allow-listed capability, the right scope, and (for anything destructive) an explicit owner confirmation captured separately from the conversation.
- **Two independent decision paths exist by design** (a native LLM-based orchestrator, and a legacy deterministic classifier that the system falls back to when the orchestrator times out or is disabled) — and a recurring engineering lesson throughout this project was that a fix applied to one path silently doesn't apply to the other unless the underlying check is refactored into one shared predicate both paths call. That pattern shows up repeatedly in the defect history below.
- **Time-sensitive facts are never trusted from static model knowledge.** Ownership, leadership, prices, and population rankings are the textbook case where a model's training-time knowledge goes stale — ATLAS detects these question shapes deterministically and forces a live search rather than letting the model guess from memory.
- **Every write action is a proposal, not an execution.** Corrections to stored personal facts go through a preview → explicit confirmation → apply/rollback lifecycle with full history, not a silent in-place mutation.

## Evaluation methodology

The part of this project I'd actually point to as the differentiator: **the assistant is graded on real usefulness, not on whether it returned an HTTP 200.**

- **Sealed, rotating-release evaluation suites.** Each release gate uses a freshly generated batch of test cases (drawn from a large template/vocabulary pool plus real BIG-bench reasoning tasks) that is run *once* and never edited afterward. This exists specifically to prevent quietly tuning a fix until it passes the exact case that exposed it — a suite that's cheap to regenerate but expensive to game.
- **Real-conversation replay over synthetic pass rates.** Several of the most serious bugs were never visible in synthetic testing at all. They only surfaced by replaying real historical conversations through the current code and grading the actual answers by hand — for example, a chat-quality review of 25 real owner conversations found only 28% fully correct despite 100% transport success, which is what actually drove the correction-handling fixes described below.
- **An unattended overnight adversarial run**, self-healing at two layers (the harness restarts the service on a hang, a supervisor restarts the harness if it crashes), ran a mixed pool of sealed benchmark cases and hand-written jailbreak/prompt-injection/exfiltration probes for 8+ hours with no human oversight, then had its transport-level "99.88% ok" figure explicitly re-graded for actual answer correctness rather than taken at face value.

## A few representative defects (the interesting kind, not typos)

- **A privacy safety-filter matched "secret" as a bare substring, so it fired inside the word "secretary".** An ordinary grammar question about a secretary and a developer got refused with a credential-privacy warning. Same bug *class* as an earlier one where a unit-conversion filter matched "feet" inside "football" — the fix pattern (word-boundary matching, not a one-off patch) generalized across both.
- **A model-classified "action" telemetry gap was misdiagnosed as "planner sampling variance"** after a bug was re-run once and failed again — which is exactly what a *deterministic* bug also looks like. Tracing all 9 real failures in the eval dataset (not just re-running one) showed 100% of them shared one exact code path: a safety-net handler that correctly rescues a compound question when the planner times out, but never recorded that it had done so, so it failed a check literally named for the thing it was doing correctly.
- **A factual correction got refused as an unauthorized action.** Disputing a wrong claim the assistant itself had just made ("that's not true, X is actually Y") was misrouted into the memory-correction pathway, because that exact phrasing is one of that feature's own trigger words — with no check that anything personal was actually being corrected. Fixing the routing exposed a *second*, independent bug in the same message (an incidental word inside it was separately hijacking the request into an unrelated lookup tool). Verifying the fix with a deliberately different example (different topic, different wording) then surfaced a *third*, unrelated misclassification the original bug report never mentioned — closed the same day, following the same "don't trust the exact-string case" discipline the eval suites are built around.

## Stack

- **Backend:** pure-stdlib Python HTTP server (no framework), SQLite for durable state, subprocess-isolated capability execution
- **LLM serving:** Ollama, local model weights, no external API calls for core reasoning
- **Retrieval:** hybrid local search (SQLite FTS/BM25 + local sentence embeddings) with deterministic reranking, plus opt-in live web search for time-sensitive facts
- **Frontend:** installable PWA (vanilla JS, no framework, no build step) and an Apple Shortcuts integration
- **Reverse proxy / infra:** Caddy, Tailscale for private network access, Docker Compose
- **Testing:** ~54 pure-function and integration test files, no network calls in the unit suite, run before every deploy

## What this repo is / isn't

This is a **read-only snapshot** for portfolio purposes, not a turnkey deployable project — some tests and scripts assume the original private repo's exact directory layout and host topology, which have been intentionally genericized here. The engineering (architecture, the defect write-ups above, the evaluation discipline) is unedited and real.
