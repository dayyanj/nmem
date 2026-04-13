"""
Hook handler — processes Claude Code hook events and writes to nmem.

Each hook script (session_start, post_tool_use, session_end) calls into
this module. The handler connects to the nmem database directly (not via
MCP) for minimal latency.

All operations are designed to be fast (<100ms) to avoid slowing down
Claude Code. Embedding is deferred to the background consolidation cycle.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Session observation collector — accumulates observations in a temp file
# so session_end can summarize them.
_SESSION_FILE_TEMPLATE = "nmem_session_{}.jsonl"


def get_session_file() -> Path:
    """Get the temp file path for the current session's observations."""
    session_id = os.environ.get("CLAUDE_SESSION_ID", "default")
    return Path(tempfile.gettempdir()) / _SESSION_FILE_TEMPLATE.format(session_id)


def append_observation(observation: dict) -> None:
    """Append an observation to the current session's temp file."""
    session_file = get_session_file()
    with open(session_file, "a") as f:
        f.write(json.dumps(observation) + "\n")


def read_observations() -> list[dict]:
    """Read all observations from the current session's temp file."""
    session_file = get_session_file()
    if not session_file.exists():
        return []
    observations = []
    for line in session_file.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                observations.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return observations


def cleanup_session_file() -> None:
    """Remove the session's temp file."""
    session_file = get_session_file()
    try:
        session_file.unlink(missing_ok=True)
    except OSError:
        pass


def handle_session_start(hook_input: dict) -> dict | None:
    """Handle session start — inject relevant memory context.

    Claude Code hook type: PreToolUse or a startup hook.
    Returns a dict with "result" key containing system prompt addition,
    or None if no context is available.
    """
    from nmem.hooks.config import load_hook_config

    config = load_hook_config()
    if not config.get("enabled", True):
        return None

    # Build context query from the session's working directory
    cwd = os.environ.get("CLAUDE_CWD", os.getcwd())
    project_name = Path(cwd).name

    try:
        import asyncio
        context = asyncio.run(_build_session_context(project_name, cwd))
        if context:
            return {"result": context}
    except Exception as e:
        logger.debug("Session start context failed (non-fatal): %s", e)

    return None


async def _build_session_context(project_name: str, cwd: str) -> str | None:
    """Build memory context for session injection."""
    from nmem import MemorySystem
    from nmem.cli.config_loader import load_config

    config = load_config()
    # Use project scope if available
    scope = os.environ.get("NMEM_PROJECT_SCOPE", f"project:{cwd}")
    config = config.model_copy(update={"project_scope": scope})

    mem = MemorySystem(config)
    await mem.initialize()
    try:
        ctx = await mem.prompt.build(agent_id="claude-code", query=project_name)
        return ctx.full_injection if ctx.full_injection else None
    finally:
        await mem.close()


def handle_post_tool_use(hook_input: dict) -> None:
    """Handle post-tool-use — capture interesting tool calls as observations.

    Receives the tool name and input/output from Claude Code's hook system.
    Writes fast to a temp file; actual memory writes happen at session end.
    """
    from nmem.hooks.config import load_hook_config
    from nmem.importance import classify_tool_importance

    config = load_hook_config()
    if not config.get("enabled", True):
        return

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    tool_output = hook_input.get("tool_output", "")

    # Check per-tool-type capture toggles from config
    tool_lower = tool_name.lower()
    if tool_lower in ("read", "glob", "grep") and not config.get("capture_reads", False):
        return
    if tool_lower in ("edit", "write") and not config.get("capture_edits", True):
        return
    if tool_lower == "bash" and not config.get("capture_bash", True):
        return

    # Classify importance — None means skip
    importance = classify_tool_importance(tool_name, tool_input)
    if importance is None:
        return

    # Check skip patterns from config
    skip_paths = config.get("filters", {}).get("skip_paths", [])
    skip_commands = config.get("filters", {}).get("skip_commands", [])

    file_path = tool_input.get("file_path", "")
    if any(p in file_path for p in skip_paths):
        return

    command = tool_input.get("command", "")
    if any(command.strip().startswith(c) for c in skip_commands):
        return

    # Build observation
    observation = {
        "timestamp": datetime.utcnow().isoformat(),
        "tool_name": tool_name,
        "importance": importance,
    }

    # Extract key parameters based on tool type
    tool_lower = tool_name.lower()
    if tool_lower in ("edit", "write"):
        observation["file_path"] = file_path
        observation["summary"] = f"Edited {Path(file_path).name}" if file_path else "File edit"
    elif tool_lower == "bash":
        # Truncate long commands
        observation["command"] = command[:200]
        observation["summary"] = f"Ran: {command[:100]}"
        # Check for failure signals in output
        output_str = str(tool_output)[:500] if tool_output else ""
        if any(kw in output_str.lower() for kw in ["error", "failed", "exception", "traceback"]):
            observation["importance"] = max(importance, 6)
            observation["outcome"] = "error"
        else:
            observation["outcome"] = "success"
    elif tool_lower == "grep":
        pattern = tool_input.get("pattern", "")
        observation["summary"] = f"Searched for: {pattern[:100]}"

    append_observation(observation)


def handle_session_end(hook_input: dict) -> None:
    """Handle session end — summarize session and write to journal.

    Collects all observations from this session, generates a summary,
    and writes it as a journal entry.
    """
    from nmem.hooks.config import load_hook_config

    config = load_hook_config()
    if not config.get("enabled", True) or not config.get("session_summary", True):
        cleanup_session_file()
        return

    observations = read_observations()
    if not observations:
        cleanup_session_file()
        return

    try:
        import asyncio
        asyncio.run(_write_session_summary(observations, config))
    except Exception as e:
        logger.debug("Session summary failed (non-fatal): %s", e)
    finally:
        cleanup_session_file()


async def _write_session_summary(observations: list[dict], config: dict) -> None:
    """Write session summary to journal."""
    from nmem import MemorySystem
    from nmem.cli.config_loader import load_config

    nmem_config = load_config()
    cwd = os.environ.get("CLAUDE_CWD", os.getcwd())
    scope = os.environ.get("NMEM_PROJECT_SCOPE", f"project:{cwd}")
    nmem_config = nmem_config.model_copy(update={"project_scope": scope})

    mem = MemorySystem(nmem_config)
    await mem.initialize()

    try:
        # Build summary from observations
        summary = _build_summary_text(observations)
        max_importance = max(o.get("importance", 3) for o in observations)
        # Session importance: average of top 3, capped at observed max
        top_importances = sorted([o.get("importance", 3) for o in observations], reverse=True)[:3]
        session_importance = min(round(sum(top_importances) / len(top_importances)), max_importance)
        # Read-only sessions are low importance
        has_edits = any(o.get("tool_name", "").lower() in ("edit", "write") for o in observations)
        if not has_edits:
            session_importance = min(session_importance, 3)

        await mem.journal.add(
            agent_id="claude-code",
            entry_type="session_summary",
            title=_build_summary_title(observations),
            content=summary[:800],  # Cap at ~200 tokens
            importance=session_importance,
            tags=["auto_capture", "session_summary"],
            compress=False,
        )

        # Also write individual high-importance observations
        for obs in observations:
            if obs.get("importance", 0) >= 6:
                await mem.journal.add(
                    agent_id="claude-code",
                    entry_type=_obs_to_entry_type(obs),
                    title=obs.get("summary", "")[:300],
                    content=json.dumps(obs)[:500],
                    importance=obs["importance"],
                    tags=["auto_capture"],
                    compress=False,
                )
    finally:
        await mem.close()


def _build_summary_title(observations: list[dict]) -> str:
    """Build a concise session title from observations."""
    # Count operations
    edits = sum(1 for o in observations if o.get("tool_name", "").lower() in ("edit", "write"))
    commands = sum(1 for o in observations if o.get("tool_name", "").lower() == "bash")
    total = len(observations)

    # Get unique edited files
    edited_files = set()
    for o in observations:
        if o.get("tool_name", "").lower() in ("edit", "write"):
            fp = o.get("file_path", "")
            if fp:
                edited_files.add(Path(fp).name)

    if edited_files:
        files_str = ", ".join(sorted(edited_files)[:3])
        if len(edited_files) > 3:
            files_str += f" +{len(edited_files) - 3} more"
        return f"Session: edited {files_str} ({total} ops)"

    return f"Session: {total} operations ({edits} edits, {commands} commands)"


def _build_summary_text(observations: list[dict]) -> str:
    """Build summary text from observations."""
    lines = []
    for obs in observations:
        summary = obs.get("summary", "")
        importance = obs.get("importance", 3)
        outcome = obs.get("outcome", "")
        if outcome == "error":
            lines.append(f"- [!] {summary}")
        elif importance >= 6:
            lines.append(f"- [*] {summary}")
        else:
            lines.append(f"- {summary}")

    return "\n".join(lines[:30])  # Cap at 30 observations


def _obs_to_entry_type(obs: dict) -> str:
    """Map observation to journal entry type."""
    tool = obs.get("tool_name", "").lower()
    if tool == "bash":
        cmd = obs.get("command", "").lower()
        if any(kw in cmd for kw in ["deploy", "docker", "systemctl"]):
            return "deployment"
        if any(kw in cmd for kw in ["pytest", "test"]):
            return "test_run"
    if tool in ("edit", "write"):
        return "file_edit"
    return "observation"
