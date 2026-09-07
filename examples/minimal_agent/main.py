"""Minimal nmem agent — a pure-cognition "scout" with no external actuator.

The whole point of nmem.agent_core: standing up a new agent is FOUR things (see
README.md), and none of them is cognition code:

  1. agent.yaml        — structural config (DB, LLM/embed endpoints, graph, loops)
  2. capabilities.env  — which nmem / nmem-sym capabilities are on (read from env)
  3. persona.py        — who this agent is (objectives, world topics, baseline KB)
  4. this main.py      — ~20 lines: build the runtime and run it

This agent passes NO executor, so it is a pure thinker: it consolidates, dreams,
forms drives and goals, and grows its symbol graph — but does not act in the world.
Give it an nmem-act ActionExecutor (via build_executor=...) and a build_proposal to
turn it into an actor (see michelle-ai for a computer-use example).

Run:
    set -a; . ./capabilities.env; set +a      # load capability flags into env
    python main.py ./agent.yaml
"""
import asyncio
import signal
import sys

import yaml

from nmem.agent_core import AgentRuntime

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from persona import build_persona  # noqa: E402


async def run(config_path: str) -> None:
    with open(config_path) as f:
        config = yaml.safe_load(f)

    runtime = AgentRuntime(config, build_persona())   # no executor -> pure thinker
    await runtime.start()
    print("agent up:", runtime.status)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await runtime.stop()


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else "agent.yaml"))
