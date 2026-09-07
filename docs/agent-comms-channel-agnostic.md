# Channel-agnostic communication + comms-learning (B3) — design

**Status:** design (build gated on founder review). **Written:** 2026-09-07.
**Sweep item:** Cluster B / B3 `NMEM_SYM_COMMUNICATION_DRIVE_ENABLED`. **Companions:**
[capability-activation-sweep.md](./capability-activation-sweep.md), [nmem-agent-core-plan.md](./nmem-agent-core-plan.md).
**Blast radius:** a NEW agent-core module (built in michelle as `service/communication.py`, extractable
verbatim to `nmem-agent-core`); nmem-sym unchanged (reuses its existing comms seams); default-off
(gated on the communication drive flag + a registered sink).

## Background & context (read first, for independent review)

**The nmem family & goal.** nmem (memory + skills) / nmem-sym (symbol graph: drives, goals,
pending-utterances, prediction) / nmem-act (actuation). Being matured into a reusable
`nmem-agent-core` so agents are thin `data + adapters + config` shells; logic + LLM-enrichment live in
the libs, toggled by flags — not forked per bot. `michelle-ai` (Gemma-4 peer to DJ-AI, computer-use
sandbox) is the live proving ground. A future **`nmem-twin`** (the dj-twin, generalized) will be a
second agent whose channel is *voice* — the acid test for channel-agnosticism.

**What nmem-sym already provides (the seams — no changes needed):**
- **When to speak:** the `communication` drive fires a `communicate` **Intent** on accumulated
  pressure; a host subscribes via `drives.on_intent(handler)` (the drive is inert without one).
- **What to say (candidates):** the surprise→utterance pipeline (B1 `OUTCOME_SURPRISE` → B2
  `PENDING_UTTERANCES`) stores worth-scored, recipient-relevance-scored rows in
  `symbol_pending_utterances` (status `pending|delivered|answered|ignored|dropped`).
- **Closing the loop:** `bridge.record_comms_response(utterance_id, source, action_type,
  outcome_strength, answered)` — marks the row answered/ignored, folds the score into a per-
  `(comms:source, action_type)` value-EWMA, and (source `drive:*`) **discharges the communication
  drive by the score** (honest discharge — an ignored utterance relieves nothing).

**Why this design.** Two requirements the naive version misses:
1. **Channel-agnostic** — the same base agent must speak over michelle's peer-exchange bus, an
   nmem-twin's voice pipeline, a log, or Slack with zero core changes (lift-and-shift for the core).
2. **Learn what communication works** — a binary/scalar "answered?" throws away the signal. The agent
   should assess *how* an utterance landed and get better at *what/how/when* it communicates — the
   same capture→surface→apply learning we built for tool-use (#2/#3), applied to communication.

## Layering
| Layer | Owns | Channel-aware? |
|---|---|---|
| **nmem-sym** (done) | *when* (drive→`communicate` intent), *what candidates* (pending utterances), *close+discharge* (`record_comms_response`) | No |
| **agent-core** (`communication.py`, new) | orchestration: intent → select utterance → render → deliver(sink) → **assess response → learn** → discharge | No |
| **host adapter** (per agent) | one `ChannelSink`: send over its channel + capture the raw response | **Yes** |

## Contracts

### ChannelSink (the only channel-specific code)
```python
class ChannelSink(Protocol):
    async def deliver(self, utterance: Utterance) -> bool:
        """Send utterance.text over THIS channel. Return True if delivered.
        When/if the recipient responds (sync or async), the sink captures the raw
        response and calls utterance.record_response(raw_text, latency_s=…).
        A channel with no response signal (log) simply never calls it."""
```
`Utterance` carries channel-neutral fields (`text`, `context` dict: source_drive, worth, recipient
hint, optional modality extras) + a bound `record_response(...)` that routes into the core assessor.
Text is the interchange; a voice sink TTS's it, a peer sink sends it, a log sink writes it. Richer
modality needs (e.g. twin emotion tags) ride in `context` as optional fields a sink reads or ignores.

### CommsAssessment (channel-neutral, produced by the CORE — reusable, learnable)
The sink hands back only raw response text + latency. The core LLM-judges it into:
```
engagement: ignored | acknowledged | answered | acted_on   # ordinal, not binary
valence:    dismissive ↔ neutral ↔ appreciated             # [-1, 1]
usefulness: did it help the recipient / advance the goal?  # [0, 1]
latency_s, raw_text
```
- Rolls up to a scalar `outcome_strength ∈ [0,1]` → `record_comms_response` (discharge unchanged).
- Retained IN FULL for learning. LLM-judge with a scalar fallback when no backend (like #4/#5).

### The comms-learning loop — REUSE the skill machinery (#2/#3), don't rebuild
Attribute each assessment to the utterance's *features* (source-drive, content-type, phrasing style,
timing bucket, recipient) and record a **comms-skill** through `mem.skills.record` under a
`communication:` tag/scope — so it inherits canonicalization (dedup paraphrased lessons), salience
ranking, and `skill.chronic` escalation FOR FREE:
- **capture:** "terse findings to DJ-AI land well" / "long unsolicited messages get ignored".
- **surface:** at compose time, recall the top comms-skills (salience-ranked) to steer phrasing/selection.
- **apply/escalate:** a comms pattern that keeps landing badly trips `skill.chronic` → change approach.
Result: the agent learns to act better (tool skills) AND communicate better (comms skills), through
one infrastructure.

## The generic loop (agent-core `communication.py`)
On a `communicate` intent:
1. **select** the top deliverable pending utterance (query `symbol_pending_utterances` by
   worth × recipient-relevance; `None` → nothing worth saying, relieve minimally / defer).
2. **compose/render** its text — `render_utterance` (+ optional LLM phrasing), steered by surfaced
   comms-skills.
3. **deliver** via the injected `ChannelSink.deliver(utterance)`; mark `delivered`.
4. **assess** (async): when the sink reports a raw response → core LLM → `CommsAssessment`.
5. **learn + close:** record the feature-attributed comms-skill; derive the scalar →
   `record_comms_response` (value-EWMA + drive discharge). No response ever → times out to `ignored`
   (low score) after a TTL, so silence teaches too.

## Host adapters
- **michelle → `PeerExchangeSink`:** `deliver` = send text over nmem-exchange to DJ-AI; capture DJ-AI's
  reply (async) → `record_response`. Adapter #1.
- **nmem-twin → `VoiceSink`** (future): `deliver` = push text into the twin's TTS/pipecat pipeline;
  capture the human's spoken reply via VAD/STT → `record_response`. Same core, one new adapter.
- **`LogSink`:** `deliver` = write; never reports a response (fire-and-forget).

## Lift-and-shift proof
Core imports nothing channel-specific (just the `ChannelSink` protocol + nmem-sym's intent/pending/
response APIs + `mem.skills`). Adding a channel = one `deliver()` + its response capture. The
assessment + learning are identical across michelle, nmem-twin, and any future agent.

## Verification
1. **Unit:** stub `ChannelSink` (records deliver calls, feeds a scripted raw response) + stub LLM
   assessor → assert: utterance selected + delivered; response assessed → scalar → discharge;
   comms-skill recorded with the right features; no-response TTL → `ignored`/low score; disabled/no-sink
   → inert.
2. **Live (michelle peer channel):** enable `COMMUNICATION_DRIVE` + register `PeerExchangeSink`. On a
   surprise → pending utterance → the drive delivers it to DJ-AI; DJ-AI's reply gets assessed; a
   comms-skill is recorded; the communication drive discharges by the score. Confirm over a few
   exchanges that comms-skills accrue and surface.

## Open questions for review
- **Q1 — Where the generic loop lives now:** build in michelle `service/communication.py` (extractable
  later) vs start the `nmem-agent-core` package now. *Lean: michelle now — consistent with #1-5; extract
  once a 2nd agent (nmem-twin) needs it.*
- **Q2 — Comms-skill store:** reuse `mem.skills` under a `communication:` tag (gets #2 dedup/salience/
  chronic free) vs a dedicated comms-memory. *Lean: reuse skills.*
- **Q3 — Utterance source:** deliver B2's surprise-derived pending utterances only, or also let the
  drive compose fresh (LLM) from current context? *Lean: pending-utterances first (grounded in real
  surprise); fresh-compose is a later extension.*
- **Q4 — Assessment cost/timing:** LLM-judge every response (rich, one call per reply) vs cheap
  heuristic + periodic LLM. *Lean: LLM-judge per response (replies are infrequent; quality matters),
  scalar-heuristic fallback when no backend.*
