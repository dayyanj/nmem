# minimal_agent — stand up an nmem agent in four files

This is the copy-me starting point for a new [`nmem.agent_core`](../../src/nmem/agent_core)
agent. The thesis: **an agent = data + adapters + config.** All cognition — memory,
drives, goals, learning, recall, consolidation, dreaming — lives in the nmem libraries
and is toggled by flags. You supply who the agent is and (optionally) how it acts.

## The four things

| File | What it is | You edit |
|---|---|---|
| [`agent.yaml`](./agent.yaml) | structural config — DB DSN, LLM/embed endpoints, symbol-graph domain, loop cadences | ✅ |
| [`capabilities.env`](./capabilities.env) | which nmem / nmem-sym capabilities are on (read from env) | ✅ |
| [`persona.py`](./persona.py) | who this agent is — objectives, world-seed topics, baseline KB (pure DATA) | ✅ |
| [`main.py`](./main.py) | ~20 lines: build `AgentRuntime(config, persona)` and run it | rarely |

Nothing here is cognition code. `AgentRuntime` owns the whole boot → run → shutdown
lifecycle and the cognitive wiring; it is **headless** (no web server) — wrap it with
whatever I/O you want.

## Run it

1. Create the agent's own Postgres database (isolated memory), e.g. `scout`.
2. Point `agent.yaml` at that DB and at an OpenAI-compatible LLM endpoint (vLLM,
   LM Studio, or a hosted API). Keep the embedding `dimensions` matching your model
   (384 for `all-MiniLM-L6-v2`).
3. Boot:

   ```bash
   set -a; . ./capabilities.env; set +a     # load capability flags into the env
   python main.py ./agent.yaml
   ```

On first boot the runtime bootstraps the agent's nmem + symbol-graph tables, seeds the
persona (objectives → goals, baseline facts → shared memory), and starts the
consolidation + drive loops. It will consolidate, dream, and form goals on its own.

## Make it an actor

This example is a **pure thinker** (no executor). To let it act in the world, give it
an nmem-act `ActionExecutor` and a proposal builder:

```python
runtime = AgentRuntime(
    config, build_persona(),
    build_executor=lambda bridge: build_my_runner(bridge),  # -> nmem_act.ReferenceRunner
    build_proposal=my_build_proposal,                        # PursuitGoal -> ActionProposal
)
```

and add a `pursuit:` section to `agent.yaml`. See `michelle-ai` for a full example
whose executor drives a computer-use sandbox.

## Add I/O

`AgentRuntime` is headless. Reach into `runtime.mem` / `.graph` / `.bridge` / `.backend`
from whatever interface you build — a FastAPI server (michelle), a CLI, a voice
pipeline. The runtime doesn't care.
