"""
nmem auto-capture hooks for Claude Code.

Hook scripts that automatically capture knowledge during Claude Code sessions
without requiring explicit memory_store calls.

Three hooks:
  - session_start: inject relevant memory context on session start
  - post_tool_use: capture edits, bash commands, and significant operations
  - session_end: compress session into a summary journal entry

Install with: nmem setup --hooks
"""
