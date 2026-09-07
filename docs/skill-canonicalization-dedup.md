# Skill canonicalization, dedup & the capture→surface→apply loop — design

**Status:** design (build gated on founder review). **Written:** 2026-09-07.
**Validated by:** michelle-ai Test 3 + direct measurement on her live skill store.
**Blast radius:** `nmem` lib — DJ-AI depends on it, so every change is **default-off / additive**
(new column nullable; new behavior behind a flag) → byte-identical until an agent opts in.

## Framing: dedup is one symptom of a leaking learning loop
The founder's question — *"if there are 116 of the same lesson, why is she not learning not to
repeat the mistake? Are we surfacing relevant lessons?"* — is the right frame. The 116 duplicates
are a **symptom**. The learning loop has three stages and leaks at each:

- **Capture** — no "already known" gate: reflection re-derives *"avoid repetitive action"* every
  session and records it fresh (0.85 dedup misses paraphrases → 116 rows). Not consolidating; re-noting.
- **Surface** — lessons ARE surfaced (a top-5 preamble via `recall_lessons`, an in-session
  programmatic loop-breaker in the sandbox harness, and the VLM's own no-loop prompt) BUT: the recall
  query is a FIXED generic string (matched to the goal, not the failure mode), and `find()` ranks by
  **cosine only** (`ORDER BY (trial_count>0) DESC, distance`) — it IGNORES reinforcement magnitude,
  so a lesson learned 116× ranks no higher than one learned once, and the 116 trial=1 duplicates
  crowd the top-5 with near-identical variants. **Sprawl weakens surfacing.**
- **Apply** — the real bottleneck: the sandbox VLM keeps looping despite surfacing + nudge, because
  it's a small vision model prone to deterministic ruts (`sandbox/harness/vlm.py`). A preamble to a
  weak actuator doesn't durably change behavior; the loop-breaker only *nudges*, never hard-stops.

So: the knowledge isn't the gap — the **actuator** is, and the sprawl makes surfacing worse. The fix
is to close the loop, of which dedup (below) is the first, enabling move: coalescing turns the 116
rows into ONE skill whose `trial_count` becomes a real "I keep hitting this" counter — which then
powers salience-ranked surfacing (layer 4) and repeat-escalation (layer 5).

## The problem (measured, not assumed)

`SkillManager.record()` already coalesces near-duplicates: it embeds `what`, and if an active skill
is cosine ≥ `dedup_threshold` (default **0.85**) it reinforces instead of inserting
(`skills.py:_find_duplicate`). Yet michelle's store sprawled to **396 skills, avg 1.15 trials**.
Measured why on her live data:

- **116 of 396** skills are ONE lesson — *"don't repeat an action that didn't change state"* —
  re-phrased by the reflection LLM every session ("repetitive clicking…", "Repeatedly using
  [scroll]…", "repetitive scrolling without new visual information…").
- Their **pairwise cosine is 0.60–0.80** — all below the 0.85 bar, so none coalesce.
- **No single threshold fixes it.** Two forces overlap: the shared boilerplate prefix
  (`"computer-use browser sandbox research: "`) inflates *every* pair toward each other, while
  paraphrase variance spreads *same-lesson* pairs apart. So dup-variants (0.6–0.8) sit in the SAME
  cosine range as genuinely distinct skills. Lowering the threshold would over-merge real skills
  (bad for DJ-AI too); raising it keeps the sprawl.

**Root cause:** the raw LLM lesson string is a poor dedup key. MiniLM cosine of two English
paraphrases of one idea is ~0.7, not >0.85. Robust dedup needs **canonicalization** — reduce a
lesson to a low-entropy canonical form and dedup on THAT — not a threshold tweak.

## Design — three additive layers, all default-off

### 1. Canonical-key dedup in `record()` (cheap, no LLM)
`record(..., canonical_key: str | None = None)`. When supplied, dedup on an **exact** active-skill
`canonical_key` match (scoped) BEFORE the embedding pass → paraphrases with the same key coalesce
for free (no embedding, no threshold). Persist the key so future records match it.

- **Storage:** add a nullable `canonical_key String(200)` column to `SkillModel` (nmem owns its own
  migrations; nullable → back-compat). *Alt considered:* reuse `name` — migration-free but overloads
  a display field and collides with the existing `name = what[:80]` default; rejected for clarity.
- **Off by default:** `canonical_key=None` → today's embedding-only path, unchanged.

### 2. Native LLM canonicalization (toggled) — "LLM enrichment as a function of nmem"
`skills.canonicalize_enabled` (default `False`) + an **optional injected LLM callable** on the
MemorySystem (host-provided, exactly like the embedder is today). When on and the caller didn't pass
a `canonical_key`, `record()` derives one by LLM-normalizing `what` → a short, controlled canonical
lesson (e.g. all three "repetitive…" variants → `"avoid-repeating-ineffective-action"`), then dedups
on it (layer 1). Off, or no LLM handle → today's behavior exactly.

- **Keep it off the hot write path.** The autonomy layer's rule holds: no LLM in a latency-critical
  write. michelle's `tool_learning` calls `record()` from the *post-pursuit* outcome sink (already
  off-critical-path, pursuits take minutes), so a 1–2 s canonicalize there is fine. For other
  callers, canonicalization must be gated (this flag) and may be backgrounded — see open questions.
- **Controlled output.** Constrain the LLM to a short slug from a small, growing vocab (prompt it
  with the agent's existing `canonical_key`s so it reuses them) — otherwise canonicalization itself
  drifts and we've moved the paraphrase problem up a level. This is the crux of the build.

### 3. Periodic cluster-merge consolidation (for accumulated sprawl)
`skills.consolidate()` — group active skills by `canonical_key` (or, absent keys, by an embedding
cluster at a merge threshold) and fold each group into one survivor: sum `trial_count`/`success_count`,
keep the best `name`/`what`, mark the rest `superseded_by_id`. Idempotent; callable from the
consolidation cycle. This is what cleans michelle's existing 396 (the write-path fix only helps new
records). Also the migration path for turning today's un-keyed sprawl into keyed skills.

### 4. Salience-ranked surfacing (`find()`) + operational-context query
Two changes so the RIGHT lesson surfaces at the right moment:
- **Rank by reinforcement, not just cosine.** `find()` currently orders by `(trial_count>0) DESC,
  distance`. Add reinforcement as a real ranking term (e.g. blend `distance` with a
  `log(trial_count+1)`/`salience` factor, flag-gated `skills.rank_by_salience`) so a lesson hit 116×
  dominates a one-off at similar cosine. Only meaningful once coalescing exists (else 116 trial=1
  rows still tie). Default off → today's ordering.
- **Query the operational context, not just the goal.** michelle's `recall_lessons` uses a FIXED
  `"…: seek and verify knowledge on the web"`. It should also surface by *failure-mode* context (the
  tool + recent action pattern), so operational lessons ("avoid repeating an action") rank when she's
  about to act, not just generic goal-lessons. This is a michelle-side `recall_lessons` change, not nmem.

### 5. Repeat-escalation (the "actually learn it" signal)
Coalescing makes `trial_count` a chronic-recurrence counter. Expose it as a signal so the system
*changes strategy* instead of re-recording:
- When a skill's `trial_count` crosses a threshold (a known failure hit N× more), emit a
  `skill.chronic` event (nmem) the host can act on — michelle escalates: the sandbox loop-breaker
  goes from *nudge* to *hard-stop / tactic-switch*, or the pursuit flags a chronic actuator failure
  for a stronger model. This is where "she keeps making the mistake" becomes "she changes approach."
- Cheap first cut: `find()`/reflection surfaces `trial_count` so `tool_learning` can SKIP re-recording
  a well-known lesson ("already known ×116 — don't re-note, escalate") — directly stopping the sprawl
  at the source AND turning recurrence into an actionable signal.

## michelle wiring (once built)
Prefer **native canonicalization** (layer 2) so the intelligence lives in nmem, not the bot (founder
principle) — `tool_learning` keeps recording raw lessons; nmem canonicalizes + dedups. michelle
sets `NMEM_SKILLS__CANONICALIZE_ENABLED=true` and passes her backend as the skills LLM handle.
Fallback: if we don't want an LLM handle in nmem yet, `tool_learning` supplies `canonical_key`
itself (layer 1 only) by mapping each lesson to a small failure-mode vocab it maintains — bespoke,
but ships without touching nmem's LLM boundary.

## Verification
1. **Unit (nmem):** two records with the same `canonical_key` → one row, `trial_count==2`
   (layer 1). With `canonicalize_enabled` + a stub LLM returning a fixed slug, three paraphrases →
   one row (layer 2). `canonical_key=None` + disabled → identical to today (regression guard).
2. **Live (michelle):** enable, let new "repetitive action" lessons arrive → they land on ONE skill
   (trial_count climbs) instead of new rows. Run `consolidate()` once → the 116-variant cluster
   collapses to ~1–3; confirm DISTINCT skills ("verify URL after click", "wait for skeleton screens")
   stay separate. Target: 396 → well under 150, avg trials up from 1.15.

## Open questions for review
- **LLM handle in nmem.skills:** inject at MemorySystem construction (host passes a callable), or
  keep the LLM out of nmem and require callers to supply `canonical_key` (layer 1 only)? The former
  matches "LLM enrichment as a native nmem function if toggled"; the latter keeps nmem LLM-free.
- **Canonical vocab:** free LLM-normalized slug (prompted with existing keys to converge) vs a fixed
  enum the agent declares? Free slug is flexible but can drift; fixed enum is stable but rigid.
- **Sync-in-sink vs backgrounded** canonicalization for non-michelle callers.
- **Migration:** confirm adding a nullable column is acceptable in nmem's migration story here.
