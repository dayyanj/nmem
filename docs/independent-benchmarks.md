# Independent Benchmarks

This page indexes community-submitted benchmarks of nmem. "Independent" means
run by someone other than the core nmem maintainers, on hardware and workloads
we don't control, with methodology and raw numbers published publicly.

**No submissions yet** — be the first. The rest of this page explains how.

## Why independent benchmarks matter

The benchmark suite shipped in [`src/nmem/benchmark/`](../src/nmem/benchmark/)
tells you what *we* measured. That's useful for regression tracking across
releases, but it has a built-in bias: we chose the workloads, the hardware
the numbers come from is our hardware, and we built the thing being tested.

For nmem to be trustworthy, people outside the project need to publish
numbers against their own workloads. Some of those numbers will be worse
than ours — that's the point. Honest comparisons are how the project gets
better where theory meets practice.

## What counts as a valid independent benchmark

A benchmark is useful to the community if it's:

1. **Reproducible** — someone else with the same hardware and inputs can
   get the same results. That means you publish the nmem commit SHA, all
   config values, the dataset (or how to obtain it), and the exact commands.
2. **Comparable** — either to a prior nmem release, to a different config,
   or to another memory system. A single absolute number ("nmem does 200
   writes/sec") is less useful than a relative one ("nmem does 200 writes/sec
   vs system X at 350, both on the same box").
3. **Honest about failure modes** — if something broke, document it. If nmem
   lost on a dimension you measured, say so. Null results and negative results
   are welcome.
4. **Publicly hosted** — blog post, arxiv paper, GitHub gist, your own
   repo, anywhere the link won't rot. Pastebin or screenshots don't count.

## Suggested workloads

You can pick your own, but here are five angles that would materially help
the project. Each one stresses a different part of the system:

1. **LongMemEval / LoCoMo-style long-context recall**
   Industry-standard agent memory benchmarks. How does nmem's hierarchical
   retrieval compare to flat vector stores on multi-turn recall? See
   [Mem0's benchmark writeup](https://arxiv.org/abs/2504.19413) for the
   methodology shape we'd like to match.

2. **Write throughput and storage growth**
   Insert 10K / 100K / 1M entries and measure writes/sec, storage footprint,
   and consolidation wall-clock time. This is the easiest thing to benchmark
   and the most useful for capacity planning.

3. **Retrieval quality after consolidation**
   Seed a realistic dataset, trigger a few consolidation cycles, then compare
   top-k results before and after. The consolidation engine is supposed to
   make retrieval *better* over time — verify that claim on your data.

4. **Multi-agent knowledge propagation**
   Simulate two agents working in the same domain. Measure how long it takes
   for LTM entries from agent A to surface in agent B's searches via the
   cross-agent access promotion path. Vary `shared_promote_min_agents` and
   `shared_promote_min_access` to see what thresholds actually work for
   your workload.

5. **Provider swap comparison**
   Hold the dataset and queries constant, vary just the embedding provider
   (`sentence-transformers/all-MiniLM-L6-v2`, `openai/text-embedding-3-small`,
   `noop`) or the LLM provider used for compression / nightly synthesis.
   Measure retrieval quality and write latency as providers change. This
   tells real users which combination is worth the cost.

## Submission template

When you publish your benchmark, include at least these fields so others
can reproduce it. Copy this template into your write-up:

```markdown
### Benchmark: <short title>

**Author**: <your name / handle / affiliation>
**Date**: YYYY-MM-DD
**Write-up**: <URL>
**nmem commit**: <full SHA>
**nmem version**: `nmem --version`

**Hardware**:
- CPU: <model, cores>
- RAM: <GB>
- Disk: <type, size>
- GPU (if used for embeddings): <model>

**Configuration**:
- Database: <PostgreSQL + pgvector / SQLite>
- Embedding provider: <name>, model <name>
- LLM provider (if used): <name>, model <name>
- Relevant nmem config overrides:
  - `journal.auto_promote_importance`: <value>
  - `ltm.shared_promote_min_agents`: <value>
  - ... etc

**Dataset**:
- Source: <description, or link>
- Size: <entries / tokens / rows>
- How to reproduce: <commands or script>

**Method**:
- What you measured
- How you measured it (cold vs warm, number of runs, percentiles)
- What you compared against (another system, another nmem config, another release)

**Results**:
- Raw numbers (table or chart)
- What surprised you
- What broke
- What you'd try next
```

## How to submit

1. Publish your benchmark somewhere public that won't rot (your blog, a
   GitHub repo, a gist, an arxiv paper).
2. Open a GitHub issue on nmem titled `Benchmark: <your title>` with the
   link and a short summary of the result.
3. A maintainer will review the methodology, ask any clarifying questions,
   and add a row to the table below.

We accept benchmarks even if they reflect poorly on nmem. The only filter
is reproducibility — if the write-up is clear enough that a motivated reader
could re-run the experiment and get comparable numbers, it goes in.

## Index

Once we have submissions, they'll be indexed here chronologically.

| Date | Author | Title | nmem version | Workload | Link | Headline |
|------|--------|-------|---------------|----------|------|----------|
| _none yet_ | | | | | | |

## Questions

Open a GitHub issue with the `benchmarking` label, or post in discussions.
For methodology questions where you'd rather get an answer before spending
the time, that's a completely valid use of the issue tracker.
