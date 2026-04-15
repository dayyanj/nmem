# How nmem Works: A Mental Model

A friendly guide to understanding what nmem does and why it matters — no code required.

## The Consultant

Imagine a brilliant consultant who just started at a new company. She graduated top of her class, read every textbook, and can answer almost any question about her field. But she just arrived — she doesn't know what happened last week, who the key people are, or what decisions were made before she walked in.

That's an LLM. Smart, well-trained, and amnesiac. Every conversation starts from zero.

```
┌─────────────────────────────────────┐
│         The New Consultant           │
│                                     │
│  "What's the standard approach?"    │
│  → Excellent answer (training)      │
│                                     │
│  "What did we decide last week?"    │
│  → "I don't know"                  │
│                                     │
│  "What's our client's history?"     │
│  → "I wasn't here"                 │
└─────────────────────────────────────┘
```

She needs a way to remember.

## The Notebook System

So she sets up a notebook system. Not just one notebook — a whole desk, because different kinds of knowledge need different treatment.

### The pocket notebook (Journal)

During the day, she scribbles quick notes about everything that happens. Client called. Meeting decision. Something unusual in the data. Fast, unfiltered — everything goes in. She doesn't worry about organisation; that comes later.

### The filing cabinet (Long-Term Memory)

Every week she reviews her pocket notebook. Important patterns get written up properly and filed. "Client X has called three times about the same issue — escalation risk." Quick observations become structured knowledge. Junk gets tossed. Duplicate notes get merged.

### The whiteboard in the break room (Shared Knowledge)

When she discovers something the whole team needs to know, she writes it on the shared whiteboard. "New company policy: all proposals need legal review." Other consultants read it. When someone else confirms it's correct, it gets a checkmark. This is knowledge that transcends any individual.

### The contact cards (Entity Memory)

One card per client, per colleague, per key contact. Updated by anyone who interacts with them. When a new consultant joins, they can flip through the cards and get up to speed quickly — they don't need to have been in every meeting.

### The policy binder (Policy Memory)

Official rules and procedures. Rarely changes, but overrides everything when it does. "Never approve discounts over 30% without VP sign-off." Consulted less often, trusted absolutely.

```
 ┌──────────┐    ┌─────────────┐    ┌──────────────┐
 │  Pocket   │    │   Filing     │    │  Break Room   │
 │ Notebook  │───→│  Cabinet     │───→│  Whiteboard   │
 │           │    │              │    │               │
 │ Today's   │    │ Patterns,    │    │ Team-wide     │
 │ notes     │    │ lessons      │    │ knowledge     │
 └──────────┘    └─────────────┘    └──────────────┘
       │                                     ▲
       │         ┌─────────────┐             │
       │         │  Contact     │   confirmed │
       └────────→│  Cards       │─────────────┘
                 │              │  (multiple people
                 │ Per-person   │   recorded the
                 │ key facts    │   same thing)
                 └─────────────┘
```

Now when someone asks "what's our history with Client X?", she doesn't guess from general knowledge. She pulls the contact card, checks her case files, and gives an answer grounded in what actually happened.

## The Nightly Review

Every evening, the consultant sits down with her notes from the day. This is the most important part of the system — it's where raw observations become useful knowledge.

She does several things:

**Promote.** A quick observation that turned out to be important gets written up properly and moved to the filing cabinet. "Noticed the website was slow" → "Website latency correlates with batch processing jobs running at 3pm — raised with engineering."

**Merge.** Three separate notes about the same topic get combined into one clear entry. Less clutter, more clarity.

**Revise.** Something she believed last month turns out to be wrong. "The budget threshold was $50K" → "Budget threshold increased to $75K as of March." The old belief gets marked as superseded, not deleted — she might need to explain to someone why old proposals used the lower number.

**Fade.** Notes about things that haven't been relevant for weeks get archived. They're not gone — if something triggers a connection later, she can dig them out. But they stop cluttering her active files.

**Synthesise.** Looking at the week's notes as a whole, she spots patterns that no individual note contained. "Three different clients mentioned competitor Y this week — are we losing ground there?" This insight gets written as a new entry in its own right.

This is what nmem calls **consolidation** and **dreamstate**. It runs automatically — the agent doesn't think about it, just like the consultant doesn't think about the mechanics of reviewing her notes. It happens, and the next morning her knowledge is sharper, more organised, and more current.

## What the Consultant Can Do Now

With the notebook system in place, the consultant's capabilities transform:

| Without nmem | With nmem |
|---|---|
| "I don't know what happened last week" | Flips to journal → "Here's what happened" |
| "I'm not sure about the current policy" | Checks the whiteboard → "Policy changed on March 5th" |
| "I'd need to research that client" | Pulls their contact card → "Here's their full history" |
| "I think the threshold is $50K" (outdated) | Belief revision caught it → "It's $75K now, changed last month" |
| Re-explains things to new team members | They read the shared whiteboard and contact cards |

The consultant is still the same person with the same intelligence. nmem didn't make her smarter — it made her **remember**. Her general training gives her the framework. Her notebook system gives her the specifics.

## The Trust Gradient

Not all knowledge is equally trustworthy, and the consultant knows this intuitively:

**Her training** (LLM): "Textbooks say X." Probably right, might be outdated, can't cite a specific source for this company.

**Her filed notes** (nmem): "I observed X on Tuesday, confirmed by data." Grounded in evidence she collected. She knows when and why she wrote it.

**Speculative notes** (marked): "I think X might be related to Y, but I haven't confirmed this." Clearly labelled. Worth investigating, not worth acting on without verification.

This is why nmem uses **grounding labels**: every piece of knowledge is tagged as `confirmed`, `observed`, `inferred`, or `speculative`. The consultant doesn't treat a hunch the same as a verified fact, and neither should an AI agent.

## When the Notebook Isn't Enough

The notebook system handles most situations well. But occasionally the consultant hits a wall.

Someone asks: "Why is our approach failing with Client Tom? We're doing everything by the book."

She checks her notes. Drug A was prescribed — sorry, Approach A was recommended. It should have improved Metric 3, but it didn't. Meanwhile, Metric 5 improved when it shouldn't have. The facts are all there in her notebook. But staring at a list of facts doesn't explain *why*.

This is where the notebook's limitation shows. It stores what happened. It retrieves what's relevant. But it doesn't **connect** facts across different entries and different contexts to generate a new explanation that nobody has written down yet.

That's the problem the companion system, [nmem-sym](https://github.com/dayyanj/nmem-sym), is designed to solve — but that's a separate story.

## Summary

nmem is the consultant's notebook system:

| Component | Analogy | What it does |
|-----------|---------|-------------|
| **Journal** | Pocket notebook | Fast capture of everything that happens |
| **LTM** | Filing cabinet | Reviewed, promoted, important patterns |
| **Shared** | Break room whiteboard | Team-wide knowledge, confirmed by multiple agents |
| **Entity** | Contact cards | Per-person/per-thing key facts |
| **Policy** | Policy binder | Official rules, rarely changes, always trusted |
| **Consolidation** | Nightly review | Promote, merge, revise, fade, synthesise |
| **Dreamstate** | Sleeping on it | Pattern detection across the week's notes |
| **Search** | Flipping through notes | Find the right information for the current question |
| **Belief revision** | Correcting old notes | Old beliefs get superseded when reality changes |
| **Social learning** | Knowledge transfer between colleagues | When multiple agents confirm the same thing, it becomes shared knowledge |

The agent is still the same LLM with the same intelligence. nmem doesn't make it smarter — it makes it **remember**. And remembering is the difference between a brilliant consultant who just arrived and one who's been here for months.
