"""Plugin-mount actuator adapter — load custom Actions from a directory.

The low-code tier: for tools that need real Python (not just a URL or an MCP server), drop a
``*.py`` file into the agent's plugins dir. Each plugin is a module exposing
``register(registry)`` that registers one or more nmem-act ``Action``s::

    # /data/agent/plugins/executors/my_tool.py
    from nmem_act import Action, CapabilityClass
    from nmem_act.registry import ActionResult

    async def _run(params):
        return ActionResult(success=True, outcome="done", observations={"echo": params})

    def register(registry):
        registry.register(Action(name="my_tool", handler=_run,
                                 capability_class=CapabilityClass.READ_ONLY,
                                 description="Echo the params.",
                                 parameters={"type": "object", "properties": {"x": {"type": "string"}}}))

Best-effort: a plugin with no ``register`` or one that raises on import is logged and skipped,
never fatal — one bad plugin can't take down agent boot.
"""
from __future__ import annotations

import glob
import importlib.util
import logging
import os

log = logging.getLogger(__name__)


def load_plugins(directory: str, registry) -> list[str]:
    """Import every ``*.py`` in `directory` and call its ``register(registry)``. Returns the
    list of plugin paths that registered successfully."""
    if not directory or not os.path.isdir(directory):
        return []
    loaded: list[str] = []
    for path in sorted(glob.glob(os.path.join(directory, "*.py"))):
        if os.path.basename(path).startswith("_"):
            continue
        mod_name = "nmem_agent_plugin_" + os.path.splitext(os.path.basename(path))[0]
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if register is None:
                log.warning("[actors] plugin %s has no register(registry) — skipped", path)
                continue
            register(registry)
            loaded.append(path)
        except Exception as e:  # noqa: BLE001 — one bad plugin must not stop agent boot
            log.warning("[actors] plugin %s failed to load: %s", path, e, exc_info=True)
    log.info("[actors] loaded %d plugin(s) from %s", len(loaded), directory)
    return loaded
