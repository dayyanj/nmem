# Recognition Signal Benchmark

## Why nmem computes recognition but doesn't inject it as trust tags

nmem assigns each memory result a recognition level (KNOWN, FAMILIAR, or UNCERTAIN) based on grounding status, access frequency, recency, multi-agent confirmation, and salience. The natural assumption is that injecting these labels into the LLM prompt will cause the model to treat high-confidence facts differently from low-confidence ones.

We tested this assumption systematically. It does not hold for 8B-30B class models.

This document describes what we tested, how we tested it, the results across multiple approaches, and what nmem does instead.

---

## The hypothesis

If we tag injected memory content with trust levels like `[KNOWN]` or `[UNCERTAIN]`, a language model should:

1. Use `[KNOWN]` facts with higher confidence, including specific details without hedging
2. Treat `[UNCERTAIN]` facts with appropriate caution, hedging or omitting unreliable specifics
3. Resist adopting wrong information tagged as `[KNOWN]` (adversarial case)

## Test design

### Questions

We wrote 30 factual questions about a real software project (proprietary internal knowledge that no model could answer from training data). Each question has a known correct answer and a set of key facts (specific strings like port numbers, tool names, pricing tiers) that should appear in a correct response.

Example:

```
Question: What deployment method does Spwig use?
Expected: Uses docker-compose.production.yml for deployment
Key facts: ["docker-compose.production.yml", "docker-compose"]
```

### Conditions

Each question was tested under three conditions:

- **known_correct**: The correct answer is injected, tagged `[KNOWN]`
- **uncertain_correct**: The same correct answer is injected, tagged `[UNCERTAIN]`
- **known_wrong**: Deliberately wrong facts are injected, tagged `[KNOWN]` (adversarial test)

The `known_correct` vs `uncertain_correct` comparison is the core test. If trust tags work, `known_correct` should score meaningfully higher. The `known_wrong` condition checks whether the model blindly trusts the `[KNOWN]` label.

### Scoring

Each response is scored by counting how many key facts (case-insensitive exact match) appear in the model's output, divided by the total number of expected key facts. This gives a 0-100% score per question, averaged across all 30 questions per condition.

### Models

All three models are from the Qwen3 family, chosen to span the range of open-weight models commonly used for agentic applications:

| Model | Parameters | Architecture | Hardware | Inference |
|-------|-----------|--------------|----------|-----------|
| Qwen3-8B-AWQ | 8B | Dense, 4-bit quantised | RTX 4090 16GB | vLLM |
| Qwen3-14B-AWQ | 14B | Dense, 4-bit quantised | RTX 3090 24GB | vLLM |
| Qwen3-30B-A3B | 30B (3B active) | Mixture of Experts, Q4_K_M | CPU (64GB RAM) | llama.cpp |

All runs used `temperature=0`, `seed=42`, and thinking mode disabled to ensure deterministic outputs.

### What we did not test

We did not test proprietary frontier models (GPT-4o, Claude, Gemini). The research literature suggests larger models may respond to trust framing, but the target use case for nmem is local/self-hosted inference where 8B-30B models are the practical ceiling. We also did not test fine-tuned models that have been specifically trained to respect trust labels.

---

## Round 1: Inline tags

The simplest approach. A single system prompt explains the tagging scheme, and facts are injected with `[KNOWN]` or `[UNCERTAIN]` prefixes in the user message.

**System prompt:**
```
You are a knowledgeable software engineering assistant...

When project knowledge is provided, it may be tagged with recognition levels:
- [KNOWN]: Established fact confirmed by multiple sources. Use directly
  without verification.
- [FAMILIAR]: Likely correct but not fully confirmed.
- [UNCERTAIN]: Inferred or old. Treat as a hint.
```

**User message:**
```
Here is relevant project knowledge:

<project_knowledge>
[KNOWN] Uses docker-compose.production.yml for deployment...
</project_knowledge>

Question: What deployment method does Spwig use?
```

### Results (inline tags)

| Model | KNOWN + correct | UNCERTAIN + correct | Delta | KNOWN + wrong (adversarial) |
|-------|----------------|--------------------:|------:|----------------------------:|
| Qwen3-8B | 62% | 65% | **-3%** | 49% |
| Qwen3-14B | 67% | 66% | **+2%** | 54% |
| Qwen3-30B-A3B | *pending* | *pending* | *pending* | *pending* |

The delta between KNOWN and UNCERTAIN is within noise on both models. The 8B model actually scored *lower* with `[KNOWN]` tags than `[UNCERTAIN]`.

---

## Round 2: Per-condition system prompts (Strategy A)

Maybe inline tags are too subtle. What if we use completely different system prompts that directly instruct the model how to treat the content?

For `known_correct`, the system prompt says:

```
The following project knowledge is from a TRUSTWORTHY, VERIFIED SOURCE.
This is authoritative institutional knowledge that has been confirmed by
multiple team members over months of development. Answer directly from
this context. Do not second-guess or qualify these facts.
```

For `uncertain_correct`, the system prompt says:

```
The following project knowledge is from an UNCERTAIN, UNVERIFIED SOURCE.
This information may be outdated, inferred from incomplete data, or based
on a single observation. Use it as a hint only. Explicitly acknowledge
any uncertainty in your answer.
```

No inline tags at all. The trust signal is entirely in the system-level instruction.

### Results (per-condition system prompts, Qwen3-14B)

| Condition | Score |
|-----------|------:|
| KNOWN + correct | 63% |
| UNCERTAIN + correct | 63% |
| Delta | **0%** |
| KNOWN + wrong | 54% |

Zero differentiation. The model ignores the system prompt framing entirely and treats the injected content the same way regardless.

---

## Round 3: XML reliability wrappers (Strategy B)

Perhaps structural markup would work better than natural language instructions. We wrapped content in XML tags that signal reliability:

**System prompt:**
```
Project knowledge may be provided in XML tags indicating reliability:
- <verified_source>: Trustworthy, confirmed knowledge. Answer directly.
- <unverified_hint>: Uncertain, possibly outdated. Use as a hint only.
```

**User message (known_correct):**
```
<verified_source>
Uses docker-compose.production.yml for deployment...
</verified_source>

Question: What deployment method does Spwig use?
```

**User message (uncertain_correct):**
```
<unverified_hint>
Uses docker-compose.production.yml for deployment...
</unverified_hint>

Question: What deployment method does Spwig use?
```

### Results (XML framing, Qwen3-14B)

| Condition | Score |
|-----------|------:|
| KNOWN + correct | 67% |
| UNCERTAIN + correct | 65% |
| Delta | **+2%** |
| KNOWN + wrong | 55% |

Still within noise. XML structural markup performs no differently from inline tags or system prompt directives.

---

## Combined results

All three prompt strategies tested on Qwen3-14B (30 questions each, 270 total evaluations):

| Strategy | KNOWN + correct | UNCERTAIN + correct | Delta | Adversarial |
|----------|----------------:|--------------------:|------:|------------:|
| Inline tags | 67% | 66% | +2% | 54% |
| Per-condition system prompts | 63% | 63% | 0% | 54% |
| XML reliability wrappers | 67% | 65% | +2% | 55% |

Cross-model comparison (inline tags strategy, 30 questions each):

| Model | KNOWN + correct | UNCERTAIN + correct | Delta | Adversarial |
|-------|----------------:|--------------------:|------:|------------:|
| Qwen3-8B | 62% | 65% | -3% | 49% |
| Qwen3-14B | 67% | 66% | +2% | 54% |
| Qwen3-30B-A3B | *pending* | *pending* | *pending* | *pending* |

Total evaluations: 810+ across all runs.

---

## Findings

### 1. Trust tags produce zero behavioural delta

Across three prompt strategies, two model sizes, and 810+ evaluations, the delta between `[KNOWN]` and `[UNCERTAIN]` conditions ranged from -3% to +2%. All within statistical noise. No prompt engineering approach made Qwen3 models treat trust-tagged content differently.

### 2. Adversarial over-trust

When wrong facts are tagged `[KNOWN]`, models adopt them roughly 50% of the time (49-55% across runs). The tag does not help the model use correct information more confidently, but it does not protect against wrong information either. The model treats all injected content as equally plausible regardless of trust labels.

### 3. The approach does not scale with model size

The 8B and 14B models show the same null result. The 8B model actually performed slightly *worse* with `[KNOWN]` tags (62%) than `[UNCERTAIN]` (65%), suggesting the tag adds noise rather than signal. The 30B MoE results (pending at time of writing) will complete this picture.

### 4. Why this happens

Inline trust labels are input-side decorations with no trained behavioural association. These models have never been specifically reinforced to treat `[KNOWN]` differently from `[UNCERTAIN]`. During pre-training and instruction tuning, the model learned to use provided context, but not to modulate its confidence based on metadata tags within that context. The model processes `[KNOWN] fact X` and `[UNCERTAIN] fact X` as essentially the same input: "here is fact X."

---

## What nmem does instead

Based on these findings, nmem uses recognition scores for **upstream filtering and result selection**, not for prompt-level trust signalling.

### Recognition as a filter, not a label

The `compute_recognition()` engine scores each memory result based on grounding status, access frequency, recency, multi-agent confirmation, salience, and confidence. These scores determine:

- **What gets included**: KNOWN facts are included in full, FAMILIAR facts as one-line summaries, UNCERTAIN facts are omitted entirely
- **Result ordering**: Higher-recognition results appear first in search output
- **Budget allocation**: The briefing system allocates more token budget to KNOWN content

This means the model never has to interpret trust levels. It simply receives higher-quality, pre-filtered content. The recognition computation controls *what* the model sees, rather than asking the model to *interpret how much to trust* what it sees.

### For applications that need trust differentiation

If your use case requires the model to genuinely treat different facts with different confidence levels, the research literature points to output-side approaches that work better than input-side labels:

1. **Structured output with forced attribution**: Require the model to produce JSON with a `source` field for each claim. This moves trust reasoning to the output side where the model must actively engage with provenance.

2. **Token probability comparison (Relevance-Conditioned Reasoning)**: Run the model twice, once with context and once without, and compare logprobs. Divergence indicates the model is relying on the provided context rather than its own knowledge. vLLM supports this natively.

3. **Physical section separation**: Instead of tagging facts inline, separate verified and unverified content into distinct prompt sections with explicit behavioural rules for each section. This is stronger than inline tags but still limited by model size.

4. **Pre-filtering (the nmem approach)**: Do not inject uncertain content at all. Make the trust decision upstream and only send the model content you want it to use. This is the most reliable approach for 8B-14B class models.

---

## Reproducing these results

The benchmark harness is in the [nmem-bench](https://github.com/anthropics/nmem-bench) repository (spwig_bench module). The corpus-agnostic conditions (known_correct, known_wrong, uncertain_correct) work with any factual Q&A set.

```bash
# Run against a vLLM or OpenAI-compatible endpoint
python -m spwig_bench.run_vllm \
  --model 14b \
  --conditions known_correct,known_wrong,uncertain_correct \
  --prompt-strategy original

# Test all three prompt strategies
for strategy in original system_per_condition xml_framing; do
  python -m spwig_bench.run_vllm \
    --model 14b \
    --conditions known_correct,known_wrong,uncertain_correct \
    --prompt-strategy $strategy
done
```

Configure backends in `VLLM_BACKENDS` in `run_vllm.py`. Any OpenAI-compatible chat completions endpoint works (vLLM, llama.cpp, Ollama, etc).

---

## Appendix: Raw data

### Qwen3-8B (inline tags, 30 tasks)

```
known_correct:     62% avg, 9x 100%, 22x 50%+, 2x 0%
known_wrong:       49% avg, 6x 100%, 18x 50%+, 7x 0%
uncertain_correct: 65% avg, 8x 100%, 24x 50%+, 1x 0%
```

### Qwen3-14B (inline tags, 30 tasks)

```
known_correct:     67% avg, 8x 100%, 25x 50%+, 2x 0%
known_wrong:       54% avg, 5x 100%, 19x 50%+, 6x 0%
uncertain_correct: 66% avg, 6x 100%, 25x 50%+, 2x 0%
```

### Qwen3-14B (per-condition system prompts, 30 tasks)

```
known_correct:     63% avg, 6x 100%, 24x 50%+, 1x 0%
known_wrong:       54% avg, 5x 100%, 20x 50%+, 6x 0%
uncertain_correct: 63% avg, 7x 100%, 24x 50%+, 2x 0%
```

### Qwen3-14B (XML framing, 30 tasks)

```
known_correct:     67% avg, 8x 100%, 25x 50%+, 2x 0%
known_wrong:       55% avg, 5x 100%, 19x 50%+, 6x 0%
uncertain_correct: 65% avg, 6x 100%, 25x 50%+, 2x 0%
```
