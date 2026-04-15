# Critique: Why Symbolic Cognition Might Not Work

A deliberately adversarial analysis of the slow brain proposal. Every claim in the design document is examined with the assumption that it's wrong until proven otherwise.

## 1. The Problem May Not Exist

The design document claims nmem lost on temporal reasoning (-0.39) and cross-agent synthesis (-0.20) because of a "structural gap" between memory and reasoning. But there are simpler explanations:

**It might just be the model.** We tested with Qwen3-14B. A 70B or frontier model might ace temporal reasoning with the same unstructured context nmem already provides. If the problem disappears at scale, the symbolic layer is a solution to a 14B limitation, not a fundamental architecture gap. We'd be building infrastructure to compensate for cheap hardware rather than solving a real problem.

**It might be the search, not the structure.** nmem's search returns results ranked by relevance, not time. For temporal questions, simply adding a "sort by date" option or a dedicated temporal query mode might recover the full -0.39 without any graph at all. That's a one-day fix versus months of graph infrastructure.

**It might be the prompt.** The baseline received raw CSV data. nmem received search results. Neither was optimised for temporal or cross-agent questions. Better prompt engineering — "arrange these facts chronologically" or "group by source agent" — could close the gap without new systems.

**The sample size is small.** Temporal reasoning had 36 evaluations across 6 time points. Cross-agent had 46. These are not large enough samples to confidently attribute the deficit to architecture. The variance between individual questions (some +2, some -3) suggests noise dominates signal.

## 2. Knowledge Graphs Have Been Tried Before — and Failed

The AI industry has spent a decade building knowledge graphs for reasoning. The track record is poor:

**Google Knowledge Graph** succeeded for factoid lookup ("How tall is the Eiffel Tower?") but never achieved reasoning over connections. Google abandoned their Freebase knowledge graph in 2016.

**IBM Watson** was built on massive knowledge graph traversal. It won Jeopardy in 2011 and then failed commercially for a decade, partly because maintaining the graph was more expensive than the value it provided. Watson Health was sold off.

**Semantic web / RDF / OWL** — the entire linked data movement promised exactly what this design describes: typed entities, typed relationships, inference over connections. After 20+ years, it's used primarily for metadata cataloguing, not reasoning.

**The common failure mode:** Knowledge graphs are expensive to build, expensive to maintain, brittle when reality changes, and deliver marginal improvements that don't justify the engineering cost. LLMs have made most knowledge graph use cases obsolete by performing soft reasoning over unstructured text.

The symbolic cognition proposal is betting against this history. What makes *this* knowledge graph different from every other one that failed?

## 3. Triple Extraction Is an Unsolved Problem

The design assumes a 14B model can reliably extract triples like:

```
[Patient Tom] ──prescribed──► [Metformin 500mg]
[Metformin] ──treats──► [Type 2 Diabetes]
```

This looks clean in examples. In practice:

**Ambiguity.** "The patient was started on metformin after considering insulin." Is that `prescribed→metformin` + `considered→insulin`? Or `prescribed→metformin` + `replaced→insulin`? The relationship type depends on clinical context a 14B model may not understand.

**Missed triples.** An entry says "BP improved after switching to amlodipine." The implicit triple `[Amlodipine] ──treats──► [Hypertension]` requires medical knowledge to extract. A 14B model might extract the explicit prescription but miss the implied therapeutic relationship.

**Wrong triples.** "Patient did NOT tolerate metformin" could be extracted as `[Patient] ──prescribed──► [Metformin]` if the negation is missed. One wrong edge in the graph propagates through spreading activation, potentially surfacing incorrect connections for months.

**Entity resolution.** "Metformin", "metformin HCl", "Glucophage", "metformin 500mg", "metformin XR" — are these the same node or five different ones? Getting this wrong either fragments the graph (too many nodes, weak connections) or merges things that shouldn't be merged (500mg and XR have different clinical profiles).

**Scale of error.** In the healthcare benchmark, 1,705 events were ingested over 180 days. Even a 5% extraction error rate means ~85 wrong or missing triples. Over months, these compound. The graph becomes a mixture of signal and noise, and spreading activation amplifies both equally.

## 4. Spreading Activation Sounds Better Than It Works

The design describes a clean activation cascade:

```
Query: "metformin"
  1 hop: [Type 2 Diabetes], [NDMA Contamination], [GLP-1 Agonists], [Insulin]
  2 hop: [Patient Tom], [DKA Event], [Sulfonylureas]
```

In reality:

**Combinatorial explosion.** At 50K nodes with an average of 5 edges each, 1-hop returns ~5 nodes, 2-hop returns ~25, 3-hop returns ~125. But real graphs aren't uniform — hub nodes (common drugs, common diagnoses) connect to hundreds or thousands of nodes. A query about "hypertension" at 2 hops could return half the graph.

**Signal dilution.** The Robert Redford example works because the path is short and specific. But most real queries activate dozens of paths, most of which are irrelevant. The LLM then receives an even larger bag of loosely connected facts than it would from a simple search. More context isn't always better — it's often worse, especially at 14B.

**Activation weighting is hard.** Which 2-hop result matters more: [Patient Tom] (via diagnosed_with→Type 2 Diabetes) or [Sulfonylureas] (via replaced_by→GLP-1 Agonists→replaces)? The design doesn't specify an activation weighting scheme. In practice, this becomes another machine learning problem — learning which paths are informative and which are noise. That's the problem we were trying to avoid by building a graph in the first place.

## 5. The Creativity Claims Are Unfalsifiable

The design's most ambitious claim is creative inference:

> "Drug A + Drug B may have complementary effects on Protein Z"

This is compelling as a story. But consider:

**How do you measure creativity?** The design proposes measuring against "known drug interaction databases." But if the interaction is already known, finding it isn't creative — it's lookup. And if it's truly novel, there's no ground truth to validate against. The metric is either trivially achievable (finding known connections) or unmeasurable (finding unknown ones).

**The base rate of useful hypotheses.** A graph with 50K nodes and 250K edges has billions of potential multi-hop paths. The vast majority connect unrelated things. "Metformin → treats → Type 2 Diabetes → diagnosed_in → Patient Tom → lives_near → Hospital_A → employs → Dr. Smith → specialises_in → Cardiology → treats → Hypertension" — is that a creative insight or noise? The system has no way to know without an LLM call for every candidate, which defeats the cost argument.

**Confirmation bias in evaluation.** When the system produces a plausible-sounding hypothesis, humans tend to rate it as creative. When it produces an implausible one, it's dismissed as noise. There's no objective boundary between "creative" and "hallucinated" — it depends entirely on whether the evaluator already suspected the connection. This makes the creativity claim untestable in practice.

## 6. The "Invisible Brain" Creates an Observability Nightmare

The design specifies that System 2 is invisible to agents:

> "The agent never knows System 2 exists; it just gets better search results"

This creates serious problems:

**Debugging.** When an agent gives a wrong answer, where did the error originate? Was it bad search results from nmem? Bad graph traversal from the slow brain? A wrong triple extracted weeks ago? The invisible design means the agent developer can't trace the reasoning chain. The "hypnosis mode" helps developers but doesn't help in production.

**Trust.** If the agent's answer quality fluctuates based on invisible background processing, users can't build reliable expectations. Sometimes the system is brilliant (slow brain found a great connection), sometimes mediocre (slow brain was idle or returned noise). This inconsistency may be worse than consistently modest performance.

**Testing.** How do you write unit tests for a system that runs asynchronously, invisibly, and produces non-deterministic outputs? The dreamstate already makes nmem hard to test. Adding another asynchronous, LLM-dependent, invisible system compounds the problem.

## 7. The Cost Model Doesn't Close

The design acknowledges the slow brain is expensive but proposes logarithmic scaling. Let's check the maths:

**Triple extraction:** 1,705 events × 1 LLM call each = 1,705 calls during consolidation. At 14B on RTX 4090, ~500ms per call = ~14 minutes. Acceptable for 180 days, but real deployments may ingest thousands of events per day.

**Hypothesis evaluation:** With O(log N) scaling at N=50K nodes, that's ~17 hypotheses per dreamstate cycle. Each requires graph traversal + LLM plausibility scoring. If cycles run nightly, that's 17 LLM calls per night — cheap. But the design also proposes curiosity-triggered evaluation during the day. If curiosity fires 10 times per hour during active use, and each trigger evaluates 5 candidate paths, that's 50 LLM calls per hour of active use. At 500ms each, that's 25 seconds of GPU time per hour — tolerable, but it adds latency to operations that are supposed to be "invisible."

**Graph maintenance:** Salience decay, archival, reactivation, entity resolution — all require periodic graph-wide scans. At 50K nodes these are fast. At 500K nodes (a year of active multi-agent use), they may not be.

**The real cost is engineering.** Building and maintaining the slow brain — extraction pipelines, graph schema, activation engine, hypothesis generator, novelty scorer, archive tier, its own dreamstate, debugging tools — is months of work. nmem's core (6 tiers, consolidation, search, MCP) took months to build and is still being refined. The slow brain is at least as complex. Is the expected improvement worth doubling the engineering surface area?

## 8. Simpler Alternatives Weren't Explored

Before building a knowledge graph with spreading activation, hypothesis generation, and its own dreamstate, consider:

**Temporal indexing.** Add a timestamp-ordered index to nmem. When a temporal question is detected, retrieve entries in chronological order instead of relevance order. This directly addresses the -0.39 temporal reasoning gap. Cost: days, not months.

**Agent-tagged retrieval.** When a cross-agent question is detected, ensure search results include entries from all agents proportionally, not just the most relevant. This addresses the -0.20 cross-agent gap. Cost: a search parameter change.

**Structured summaries.** At consolidation, generate periodic summaries: "Week 3 ED stats: 45 visits, top diagnoses: flu (15), chest pain (8)." Store these as LTM entries. Temporal and cross-agent questions now have pre-computed answers without graph traversal. Cost: additional dreamstate output, no new infrastructure.

**Bigger model.** Run the same benchmark with Qwen3-30B or 70B. If temporal reasoning improves, the problem is model capacity, not architecture. Cost: more GPU, zero engineering.

**Better prompting.** Add chain-of-thought prompting to the evaluation: "First, list all relevant facts chronologically. Then, identify trends. Then, answer the question." This scaffolds the reasoning that the slow brain tries to pre-compute. Cost: prompt engineering, zero infrastructure.

Any of these might recover part or all of the benchmark deficit at a fraction of the cost and complexity of the symbolic cognition system.

## 9. The Kahneman Analogy Is Misleading

The design uses "Thinking, Fast and Slow" as its architectural metaphor. But the analogy breaks down:

**Human System 2 is the same hardware.** Kahneman's System 2 isn't a separate brain — it's the same neural network operating in a different mode (focused attention vs. automatic processing). The proposal creates a literally separate system with different data structures, different algorithms, and different infrastructure. The analogy suggests unity; the architecture creates fragmentation.

**Human System 2 is always available.** You can engage slow thinking at any time by directing attention. The proposed slow brain is invisible and asynchronous — it may or may not have pre-computed the connection you need. If it hasn't, you get System 1's answer, which is exactly what we have today. The value of System 2 depends entirely on whether it happened to explore the right region of the graph before you needed it.

**Human System 2 doesn't hallucinate.** When you do slow, careful reasoning, you're more likely to be right than when you use System 1 intuitions. The proposed slow brain is explicitly designed to hallucinate ("that's the point"). This inverts the reliability relationship between the two systems. In the design, System 1 (fast) is reliable and System 2 (slow) is unreliable. In humans, it's the opposite. The analogy is being used to justify the architecture, but the architecture contradicts the analogy.

## 10. The Benchmark Doesn't Justify the Investment

Final numbers: nmem 3.84/5, baseline 3.60/5, +7% overall.

The symbolic cognition system is being proposed to address:
- Temporal reasoning: -0.39 (nmem 2.83 vs baseline 3.22)
- Cross-agent: -0.20 (nmem 3.83 vs baseline 4.02)

Combined, these represent 83 of 205 evaluations where nmem underperformed. If the slow brain perfectly fixed both categories (bringing them to +0.50 above baseline), the overall delta would rise from +0.24 to approximately +0.60. That's a meaningful improvement, but:

- Belief revision already scored 5.00/5 at day 120+. The slow brain can't improve that.
- Direct recall at +0.36 is already good. Marginal improvement possible but diminishing returns.
- The best case scenario for the slow brain is turning a +7% overall result into a +15% overall result.
- The worst case is that the added complexity regresses belief revision (by contaminating search results with speculative graph connections) while only partially fixing temporal reasoning.

Is +8 percentage points of potential improvement worth months of engineering, a new database layer, additional LLM inference costs, and doubled system complexity?

## Conclusion

The symbolic cognition design is intellectually elegant and grounded in real cognitive science. The Kahneman framing is compelling. The healthcare benchmark provides genuine motivation.

But intellectual elegance is not the same as engineering value. The history of knowledge graphs suggests that the maintenance cost exceeds the reasoning benefit. The triple extraction problem is harder than the design acknowledges. The creativity claims are unfalsifiable. Simpler alternatives haven't been exhausted.

The honest recommendation from a sceptic: **try the simple fixes first.** Temporal indexing, agent-tagged retrieval, structured summaries, and better prompting could each be implemented and benchmarked in days. If they close the gap, the symbolic cognition system isn't needed. If they don't — and specifically if the gap persists even with a larger model — then there's a genuine case for the graph approach, and the prototype should proceed with eyes open about the known failure modes of knowledge graphs at scale.

Build the smallest possible prototype that tests one specific claim (e.g., "spreading activation improves temporal reasoning by +0.30 on the healthcare benchmark"). If that single claim fails, stop. Don't build the creativity engine, the hypothesis generator, the archival tier, or the logarithmic dreamstate on faith. Each component should earn its place by measurably improving a benchmark score.
