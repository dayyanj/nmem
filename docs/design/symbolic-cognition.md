# Symbolic Cognition: The Slow Brain

A design concept for associative reasoning over memory — the missing layer between storage and decision-making.

## The Problem

nmem solves memory. An agent can store, consolidate, and retrieve knowledge across sessions, agents, and time. The healthcare benchmark proved this works: belief revision scores 5.00/5, direct recall +10%, pattern detection +4%.

But two categories scored worse than baseline:
- **Temporal reasoning (-0.39):** "How has ED utilization changed month over month?"
- **Cross-agent synthesis (-0.20):** "What patterns do you see across all four teams?"

These aren't memory failures. The right facts were in nmem. The failure is in **connecting** those facts — traversing relationships, ordering events, synthesising across sources. The LLM receives a bag of relevant facts and has to figure out how they relate from raw text. Sometimes it can. Often it can't, especially at 14B scale.

No memory system will fix this. No LLM will reliably do this from unstructured context. The gap is structural.

## The Human Analogy

Consider two cognitive experiences:

**Fast:** "What's the capital of France?" → "Paris." Instant. No effort. This is what nmem does — retrieve a stored fact.

**Slow:** You're watching a film and the lead actor looks familiar. You know you know them. You can almost see the other film they were in. You describe the shape of the knowledge — "older actor, Sundance, blonde in the 70s, worked with Newman" — but the name won't come. Two days later, completely out of context, you blurt out "Robert Redford."

What happened? Your brain didn't search harder. It **traversed an associative graph** — following links from "Sundance" to "festival" to "independent film" to "Redford" — but the traversal took time because the links were indirect. A random stimulus activated a node close enough to cascade to the answer.

Now consider a third experience:

**Creative:** A researcher studying two unrelated drugs notices that Drug A inhibits pathway X and Drug B activates pathway Y. Both pathways regulate protein Z. Nobody has combined them. The researcher thinks: "What if they neutralise each other's side effects while both targeting Z?" This isn't memory. It isn't pattern matching. It's **graph traversal over typed relationships** — connecting nodes that were never explicitly linked, generating a hypothesis that may be wrong but is worth testing.

Humans do this naturally. AI agents currently cannot.

## Two Brains, Not One

Kahneman's "Thinking, Fast and Slow" provides the architecture:

### System 1: The Fast Brain (nmem)

What it does today:
- Store observations, lessons, policies
- Consolidate: promote, deduplicate, revise beliefs, decay salience
- Retrieve: hybrid search (vector + FTS), entity dossiers, knowledge links
- Synthesise: nightly dreamstate detects patterns from journal activity

Characteristics:
- **Fast.** Search returns in milliseconds. Consolidation runs in seconds.
- **Cheap.** Mostly heuristic. LLM calls only for compression, dedup merge, synthesis.
- **Reliable.** Grounded in stored evidence. Belief revision prevents stale knowledge.
- **Limited.** Returns what's stored, not what's implied. Cannot infer novel connections.

### System 2: The Slow Brain (new)

What it would do:
- Maintain a **typed knowledge graph** of entities, relationships, and properties
- Perform **spreading activation** — when one node is accessed, adjacent nodes light up
- Generate **hypotheses** — "A relates to B because A→X→Y→B"
- Score **novelty** — how surprising is this connection? Has anyone made it before?
- Feed hypotheses back to the fast brain for **grounding** — does the evidence support this?

Characteristics:
- **Slow.** Graph traversal, LLM-powered inference, multi-hop reasoning. Seconds to minutes.
- **Expensive.** Requires LLM calls to evaluate candidate connections, score plausibility.
- **Unreliable.** Generates hypotheses, not facts. Will hallucinate. That's the point.
- **Creative.** The source of "what if" thinking, non-obvious connections, novel insights.

### The Interaction

```
        ┌─────────────────────────────────────┐
        │         Agent / Application          │
        └────────────┬────────────────────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
   ┌─────────────┐     ┌──────────────┐
   │  System 1   │     │   System 2   │
   │  Fast Brain │◄───►│  Slow Brain  │
   │   (nmem)    │     │  (symbolic)  │
   │             │     │              │
   │  storage    │     │  graph       │
   │  retrieval  │     │  traversal   │
   │  consolid.  │     │  hypothesis  │
   │  grounding  │     │  creativity  │
   └─────────────┘     └──────────────┘
         │                     │
         │   curiosity         │   hypotheses
         │   signals ─────────►│   need grounding
         │◄────────────────────│
         │   evidence          │
         │   for/against       │
```

The fast brain triggers the slow brain via **curiosity signals** — nmem already detects "this is interesting, I've seen something related" but currently those signals decay and die. In the two-brain architecture, curiosity signals wake System 2: "go think about this."

The slow brain returns **hypotheses** to the fast brain for **grounding** — does the stored evidence support this inference? The fast brain's belief revision machinery evaluates: confirmed, disputed, or needs investigation.

## The Symbol Graph

### What is a symbol?

A symbol is a node in the graph that represents a concept — not a memory entry, but an abstraction derived from many entries. "Metformin" is a symbol. "Hypertension" is a symbol. "Patient Tom" is a symbol. "Friday discharge pattern" is a symbol.

Symbols have:
- **Identity:** A unique node in the graph
- **Type:** entity, concept, event, property, relationship
- **Groundedness:** How many memory entries support this symbol's existence
- **Salience:** How recently and frequently has this symbol been activated

### Typed edges

Edges connect symbols with explicit relationship types:

```
[Metformin] ──treats──► [Type 2 Diabetes]
[Metformin] ──recalled──► [NDMA Contamination]
[Metformin] ──replaced_by──► [GLP-1 Agonists]    (after day 75)
[Metformin] ──interacts_with──► [Insulin]
[Patient Tom] ──diagnosed_with──► [Type 2 Diabetes]
[Patient Tom] ──prescribed──► [Metformin]
[Patient Tom] ──readmitted──► [DKA Event Day 50]
[Friday Discharge] ──causes──► [Readmission Risk]
[Friday Discharge] ──observed_by──► [Discharge Agent]
```

Edge types include: treats, causes, inhibits, activates, prescribed, diagnosed_with, observed_by, replaced_by, contradicts, correlates_with, instance_of, part_of, temporal_before, temporal_after.

### Disambiguation

Multiple entities share names. The graph handles this naturally:

```
[Tom#1: neighbour] ──lives──► [Next Door]
                   ──wears──► [Red Hat]

[Tom#2: acquaintance] ──lives──► [Other County]
```

When a new mention of "Tom" arrives, the slow brain evaluates context to determine which node it attaches to — or whether to create a third.

### Spreading activation

When the fast brain retrieves a fact about [Metformin], the graph activates adjacent nodes:

```
Query: "metformin"
  Activated: [Type 2 Diabetes] (1 hop, treats)
  Activated: [NDMA Contamination] (1 hop, recalled)
  Activated: [GLP-1 Agonists] (1 hop, replaced_by)
  Activated: [Insulin] (1 hop, interacts_with)
  2nd hop:   [Patient Tom] (via Type 2 Diabetes → diagnosed_with)
  2nd hop:   [DKA Event] (via Patient Tom → readmitted)
  2nd hop:   [Sulfonylureas] (via GLP-1 Agonists → replaces)
```

The agent asking "is metformin safe?" now gets not just the recall notice but the full context: which patients are affected, what the replacement is, and the history of protocol changes. The LLM doesn't have to infer these connections — they're pre-computed.

### Activation control: the neural gating model

Naive spreading activation is dangerous — a hub node like [Hypertension] connects to thousands of patients, drugs, and protocols. Activating everything at 2 hops returns half the graph. Real neural networks solve this with gating mechanisms. The symbol graph must do the same.

#### Threshold firing

A neuron only fires when combined input exceeds a threshold. In the graph:

- Each edge carries an **activation weight** (0.0–1.0), derived from the edge's salience, recency, and co-activation history.
- A node accumulates incoming activation from all edges that reach it in the current traversal.
- The node only **fires** (propagates to its own neighbours) if accumulated activation exceeds its **firing threshold**.
- Hub nodes (high degree) have **higher thresholds** — they require stronger signals to activate. A weak query about "blood pressure" won't cascade through [Hypertension] to thousands of patients. A strong, specific query about "hypertension treatment protocol change March 2025" will.

```
Node: [Hypertension]
  Incoming edges: 847
  Firing threshold: 0.70 (auto-scaled by degree)

Query: "metformin safety" → arrives via [treats] edge, weight 0.35
  Accumulated: 0.35 < 0.70 → does NOT fire. Hypertension acknowledged but not traversed.

Query: "hypertension protocol update" → arrives via [treats] + [protocol] edges, weight 0.45 + 0.40
  Accumulated: 0.85 > 0.70 → FIRES. Traversal continues to protocol-related neighbours.
```

This prevents the combinatorial explosion the critique identified. Most paths die at hub nodes unless the signal is strong enough.

#### Long-Term Potentiation / Depression (LTP/LTD)

Connections that fire together strengthen. Connections that go unused weaken:

- **LTP:** When a traversal path `A → B → C` produces a result that the agent actually uses (measured by whether the search result was included in the LLM's answer), the edge weights along that path increase. The path becomes easier to activate next time.
- **LTD:** Edges that are traversed but produce unused results weaken over time. The graph self-prunes noisy connections without deleting them — they fade to archive threshold.
- **Hebbian learning:** "Nodes that fire together wire together." When two nodes are consistently co-activated across different queries, a direct edge can be created between them even if they were originally connected only through intermediate nodes. This is how the graph learns shortcuts.

```
Day 1:   [Metformin] → [Type 2 Diabetes] → [Patient Tom]  (3 hops, weight 0.3)
Day 30:  Same path activated 12 times, results used 8 times
Day 31:  Edge weight strengthened to 0.7 — [Patient Tom] now fires easily from [Metformin]
Day 60:  Hebbian shortcut created: [Metformin] ──relevant_patient──► [Patient Tom] (direct edge)
```

#### Myelination: fast paths

In neuroscience, myelin sheaths insulate frequently-used axons, making signal transmission faster. In the graph:

- Edges with high LTP scores (frequently used, results valued) get **myelinated** — marked as fast paths.
- Myelinated edges are traversed first during activation, before exploring weaker connections.
- This creates a natural priority ordering: well-established connections are explored quickly, speculative connections only when the fast paths don't yield results.
- Myelinated paths can be cached in memory for sub-millisecond traversal.

#### Pathway types

Three activation patterns, modelled on neural pathway types:

**Converging pathways:** Multiple weak signals combining to activate a single node. When several edges each carry sub-threshold activation, their sum can exceed the threshold. This allows the graph to detect patterns that no single connection reveals — exactly the cross-agent synthesis scenario where triage, pharmacy, and discharge each contribute a weak signal about [Patient Tom] that converges to a strong one.

**Diverging pathways:** A single strong activation spreading to multiple targets. When a high-salience node fires, it activates several downstream paths simultaneously. This is useful for exploratory queries ("what do we know about diabetes?") where breadth matters. A **fan-out budget** limits how many downstream nodes can activate from a single source, preventing runaway cascading.

**Reverberating pathways:** Feedback loops where activation cycles back through the same nodes. In neuroscience, these sustain attention and working memory (e.g., breathing rhythm). In the graph, they represent:
- **Persistent curiosity:** A loop that keeps firing means the system keeps "thinking about" a topic. This is the trigger for System 2's deeper exploration.
- **Rumination detection:** If a loop fires more than K times without producing new results, it's dampened. Endless cycling is wasteful, just like anxious rumination.

#### Consolidation parallels

The neuroscience of pathway formation maps directly to existing nmem consolidation:

| Neural process | Graph equivalent |
|---|---|
| **Sleep consolidation** | Nightly dreamstate: replay the day's activations, strengthen productive paths, prune dead ends |
| **Exercise / new experiences** | New events ingested: create new nodes and edges, expand the graph's reach |
| **Repetition** | Repeated queries on same topics: LTP strengthens those paths |
| **Environmental cues** | Curiosity signals: external triggers that activate dormant regions |
| **Nutrition** | Data quality: better-extracted triples → cleaner graph → more reliable activation |

#### Activation budget

Even with threshold gating, the system needs a hard ceiling:

- **Max activated nodes per query:** Configurable, default 50. Once 50 nodes have fired, traversal stops regardless of remaining paths.
- **Max depth:** Default 3 hops. Deeper traversal only in explicit exploration mode.
- **Time budget:** Default 100ms. Traversal terminates if time limit is reached, returning whatever has been activated so far.
- **Diminishing activation:** Each hop reduces the signal by a decay factor (e.g., 0.6). By hop 3, a signal that started at 1.0 is at 0.216 — unlikely to exceed any threshold.

This means the worst case is bounded and predictable: 50 nodes, 3 hops deep, under 100ms. The graph can grow to millions of nodes without affecting query-time performance.

## When the Slow Brain Thinks

System 2 doesn't run on every query. It activates under specific conditions:

### Curiosity trigger

nmem's existing curiosity engine detects:
- **Déjà vu:** "I've seen something like this before but in a different context"
- **Novelty:** A new fact that doesn't fit existing patterns
- **Conflict:** Two symbols connected by contradictory edges

When curiosity fires, System 2 wakes up and traverses the graph from the triggering node.

### Scheduled exploration

During dreamstate (nightly consolidation), System 2 can:
- Traverse under-explored regions of the graph
- Look for **structural holes** — clusters of symbols that should be connected but aren't
- Generate hypotheses about missing edges
- Score and prune weak hypotheses from prior explorations

### Explicit invocation

The agent or application can ask System 2 directly: "What connections exist between [Drug A] and [Drug B]?" This triggers a targeted multi-hop traversal.

## The Creativity Engine

The most interesting capability of the slow brain is **creative inference** — generating hypotheses that nobody explicitly stored.

### How it works

1. Agent stores: "Drug A inhibits pathway X" (journal entry → entity memory → symbol edge)
2. Agent stores: "Drug B activates pathway Y" (separate entry, separate agent even)
3. Consolidation extracts symbols and edges into the graph
4. Dreamstate traversal notices: pathway X and pathway Y both connect to [Protein Z]
5. System 2 generates hypothesis: "Drug A + Drug B may have complementary effects on Protein Z"
6. Hypothesis is scored for novelty (has this connection been made before?) and plausibility (do the edge types support it?)
7. High-scoring hypotheses are stored as **speculative entries** in nmem with `grounding: speculative`
8. The fast brain's belief revision can later confirm or dispute based on new evidence

### The hallucination question

System 2 will generate wrong hypotheses. This is expected and desirable. The key is that:

- Hypotheses are **labelled** as speculative, never mixed with confirmed facts
- The fast brain **grounds** them — checking stored evidence for/against
- Wrong hypotheses **decay** through salience mechanisms
- Right hypotheses get **promoted** through the standard belief lifecycle

The system doesn't need to be right most of the time. It needs to occasionally surface a connection that no one would have found by searching.

## Architecture

### Separate system, shared database

The slow brain is not a layer inside nmem. It's a companion service:

```
nmem (fast brain)
  ├── Journal, LTM, Shared, Entity, Policy, Working  (existing tiers)
  ├── Knowledge Links (existing, flat associations)
  ├── Curiosity Engine (existing, currently underused)
  └── SymbolBridge API (new, sends signals to slow brain)

symbolic-cognition (slow brain)
  ├── Symbol Graph (PostgreSQL + typed edges, or dedicated graph DB)
  ├── Activation Engine (spreading activation, multi-hop traversal)
  ├── Hypothesis Generator (LLM-powered inference over graph paths)
  ├── Novelty Scorer (how surprising is this connection?)
  └── nmem Client (reads evidence, writes speculative entries)
```

### Why separate?

1. **Different performance profiles.** The fast brain must be fast. Adding graph traversal to every search would slow it down.
2. **Different reliability guarantees.** The fast brain is grounded. The slow brain speculates. Mixing them contaminates trust.
3. **Different scaling.** The slow brain is compute-heavy (LLM calls for each hypothesis). It should scale independently.
4. **Optional.** Not every nmem deployment needs creativity. The fast brain works alone (the healthcare benchmark proved this).

### Symbol extraction pipeline

New nmem entries flow through a symbol extractor at consolidation:

```
Journal entry: "Patient Tom prescribed metformin 500mg for Type 2 diabetes,
               started 2025-01-15. Previous medication glipizide discontinued
               due to hypoglycemia episodes."

Extracted symbols + edges:
  [Patient Tom] ──prescribed──► [Metformin 500mg]     (date: 2025-01-15)
  [Patient Tom] ──diagnosed──► [Type 2 Diabetes]
  [Patient Tom] ──discontinued──► [Glipizide]          (reason: hypoglycemia)
  [Metformin] ──treats──► [Type 2 Diabetes]
  [Glipizide] ──treats──► [Type 2 Diabetes]
  [Glipizide] ──causes──► [Hypoglycemia]              (in this patient)
```

This runs during consolidation — not on every write. The 14B model can extract triples reliably. The graph accumulates over days and weeks, building a rich associative network.

## What This Solves

### Temporal reasoning (benchmark: -0.39)

"How has ED utilization changed month over month?"

Today: nmem returns relevant facts but they're unordered. The LLM has to sort, count, and compare.

With symbols: The graph has `[Month 1] ──ed_visits──► [count: 45]`, `[Month 2] ──ed_visits──► [count: 62]`. Spreading activation from "ED utilization" traverses temporal edges and returns the trend pre-computed.

### Cross-agent synthesis (benchmark: -0.20)

"What patterns do you see across all four teams?"

Today: Search returns top results, often dominated by one agent. The LLM misses cross-agent connections.

With symbols: Each agent writes to the same symbol graph. [Patient Tom] has edges from triage, treatment, pharmacy, and discharge. Traversal from [Patient Tom] naturally surfaces the cross-agent picture. A query about "patterns" activates pattern-type nodes that multiple agents contributed to.

### The Robert Redford problem

"Who's that actor? I know this..."

Today: Search for vague descriptions returns weakly relevant results. The answer is in memory but the retrieval path is too indirect.

With symbols: Partial activation. "Sundance" activates [Sundance Film Festival] which activates [Robert Redford] via a `founded_by` edge. Even without the name, the graph finds it through association.

## Design Decisions

1. **Graph database choice.** PostgreSQL may be too slow for multi-hop traversal at scale. Three candidates need evaluation:

   | Option | Strengths | Weaknesses |
   |--------|-----------|------------|
   | **PostgreSQL** (nodes + edges + pgvector) | Shared infra with nmem, no new dependency, familiar tooling, pgvector for node embeddings | Recursive CTEs for multi-hop are expensive, no native graph query language, join-heavy at depth 3+ |
   | **Neo4j** | Purpose-built for graph traversal, Cypher query language, native multi-hop, mature ecosystem | Heavy dependency (JVM), separate deployment, data sync with nmem needed, overkill for small deployments |
   | **DGraph** | Native GraphQL, distributed, fast traversal, Go-based (lighter than JVM) | Less mature ecosystem, another operational dependency, smaller community |

   Decision: **Evaluate all three with a prototype.** Start with PostgreSQL (simplest integration), benchmark multi-hop query latency at graph sizes of 10K, 50K, 100K nodes. If PostgreSQL handles 3-hop traversal under 100ms at 50K nodes, stay with it. If not, move to Neo4j (most mature graph option). The key metric is traversal latency, not storage — the graph is read-heavy during activation.

2. **Extraction model.** Target 14B for triple extraction. Complex multi-hop relationships may need 30B+ but this requires experimentation — start with 14B, measure extraction quality, escalate only if needed. The goal is to keep the slow brain runnable on the same consumer hardware as nmem.

3. **Graph size management.** Salience decay on nodes and edges, but faded nodes don't disappear — they move to an **archival tier**. The archival store maintains all nodes and edges but with reduced activation weight. Active graph stays fast and focused. Archived connections are still traversable but require more activation energy to reach — like the Robert Redford problem, where the answer surfaces eventually through indirect activation rather than direct search. Two tiers:
   - **Active graph:** Recently activated, high-salience nodes. Fast traversal. In-memory cache candidate.
   - **Archive:** Decayed nodes. Still linked. Reachable via deep traversal or explicit exploration. Reactivated if accessed (promotion back to active).

4. **Hypothesis evaluation cost.** The slow brain needs its own dreamstate cycle, separate from nmem's nightly consolidation. The hypothesis space grows combinatorially — logarithmic approaches are essential:
   - **Log-scaled exploration budget:** As the graph grows, the number of hypotheses evaluated per cycle scales as O(log N) not O(N). Prioritise high-novelty, high-groundedness candidates.
   - **Cascade filtering:** Cheap heuristic filters (edge type compatibility, hop distance, activation overlap) before expensive LLM plausibility scoring. Most candidates are eliminated without an LLM call.
   - **Diminishing returns detection:** If a dreamstate cycle produces zero confirmed hypotheses for K consecutive runs, reduce the budget. Increase it again when new high-curiosity signals arrive.

5. **Integration surface.** System 2 is an **invisible brain** — it is not directly accessible to agents or applications. The fast brain (nmem) decides when to invoke it based on curiosity signals, conflict detection, and dreamstate scheduling. The agent never knows System 2 exists; it just gets better search results when the slow brain has pre-computed relevant connections.

   One exception: a **direct invocation API** ("hypnosis mode") allows developers and diagnostic tools to query the graph explicitly. This is for debugging, benchmarking, and edge cases where an application needs to force deep reasoning. It is not part of the normal agent workflow.

   ```
   Normal path:  Agent → nmem search → (System 2 enriches silently) → results
   Hypnosis:     Developer → System 2 API → graph traversal → hypotheses
   ```

6. **Evaluation.** The healthcare benchmark temporal reasoning (-0.39) and cross-agent (-0.20) questions are the primary test suite. But evaluation should extend beyond fixing benchmark regressions:
   - **Novel hypothesis quality:** Does the slow brain surface connections that neither agent nor developer anticipated? Measure with held-out scenarios not in the training data.
   - **Creative inference:** Can it generate "Drug A + Drug B might treat Disease R" hypotheses? Measure against known drug interaction databases for precision.
   - **Knowledge gap detection:** Does it identify structural holes in the graph — things the agents should know but don't? This becomes a proactive learning signal.
   - **Transfer learning:** When a new agent joins, does the pre-computed graph give it a richer starting context than nmem's shared knowledge alone?

## Not In Scope

- **Real-time graph traversal in the search hot path.** System 2 is slow by design. Search results can be annotated with "related symbols" asynchronously, but the primary search must remain fast.
- **Replacing the LLM.** System 2 pre-computes the reasoning scaffold. The LLM still makes the final synthesis. The graph makes the LLM's job easier, it doesn't replace it.
- **General knowledge graphs.** This is not a Wikidata-style exhaustive ontology. It's a working graph built from what the agents actually encounter — sparse, biased toward recent experience, and that's fine.

## Next Steps

1. **Prototype symbol extraction** — Add a consolidation hook that extracts triples from promoted LTM entries using the 14B model. Store in a simple nodes+edges PostgreSQL schema.
2. **Prototype spreading activation** — Given a query, activate the matched node and return N-hop neighbours with edge types.
3. **Augment search results** — When nmem returns search results, annotate each with related symbols from the graph. Measure whether the LLM answers temporal and cross-agent questions better.
4. **Run the healthcare benchmark** — Same 180 days, same questions, with symbol-augmented retrieval. Compare temporal reasoning and cross-agent scores.
5. **If positive** — Build the hypothesis generator and novelty scorer. Design the curiosity→System 2 pipeline. Consider whether this becomes part of nmem or a separate package.
