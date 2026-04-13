"""
Hook entry points — called by Claude Code's hook system.

These are thin wrappers that parse Claude Code's hook JSON from stdin
and delegate to the handler module.

Claude Code hooks receive JSON on stdin with the hook event data.
They can optionally write JSON to stdout to modify behavior.
"""

from __future__ import annotations

import json
import sys
import logging


def session_start() -> None:
    """Entry point for the session start hook.

    Claude Code calls this when a session begins. We inject relevant
    memory context into the system prompt.
    """
    _configure_logging()
    try:
        hook_input = _read_stdin()
        from nmem.hooks.handler import handle_session_start
        result = handle_session_start(hook_input)
        if result:
            _write_stdout(result)
    except Exception as e:
        logging.getLogger(__name__).debug("session_start hook error: %s", e)


def post_tool_use() -> None:
    """Entry point for the post-tool-use hook.

    Claude Code calls this after every tool call. We capture interesting
    operations (edits, bash commands) as observations.
    """
    _configure_logging()
    try:
        hook_input = _read_stdin()
        from nmem.hooks.handler import handle_post_tool_use
        handle_post_tool_use(hook_input)
    except Exception as e:
        logging.getLogger(__name__).debug("post_tool_use hook error: %s", e)


def session_end() -> None:
    """Entry point for the session end hook.

    Claude Code calls this when a session ends. We summarize the session
    and write it to the journal.
    """
    _configure_logging()
    try:
        hook_input = _read_stdin()
        from nmem.hooks.handler import handle_session_end
        handle_session_end(hook_input)
    except Exception as e:
        logging.getLogger(__name__).debug("session_end hook error: %s", e)


def _read_stdin() -> dict:
    """Read JSON from stdin (Claude Code hook input)."""
    try:
        data = sys.stdin.read()
        if data.strip():
            return json.loads(data)
    except (json.JSONDecodeError, IOError):
        pass
    return {}


def _write_stdout(data: dict) -> None:
    """Write JSON to stdout (Claude Code hook output)."""
    try:
        sys.stdout.write(json.dumps(data))
        sys.stdout.flush()
    except IOError:
        pass


def _configure_logging() -> None:
    """Configure logging to stderr (stdout reserved for hook JSON-RPC)."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(name)s: %(message)s",
    )
