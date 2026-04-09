"""
ChatGPT data export importer.

Parses the conversations.json from OpenAI's data export
(Settings > Data controls > Export data) and imports conversations
as journal entries.

The export contains a tree of messages per conversation (to handle
edits/regenerations). We walk the active branch (weight=1.0) to
reconstruct the linear conversation.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from nmem.cli.importers.base import ImportResult


def _walk_active_branch(mapping: dict) -> list[dict]:
    """Walk the active branch of a ChatGPT conversation tree.

    The mapping is a dict of {node_id: {id, message, parent, children}}.
    We find the root (parent=None), then follow children, preferring
    those with weight=1.0 (active branch).

    Returns:
        List of message objects in chronological order.
    """
    # Find root node (parent is None)
    root_id = None
    for node_id, node in mapping.items():
        if node.get("parent") is None:
            root_id = node_id
            break
    if root_id is None:
        return []

    # Walk the tree following the active branch
    messages = []
    current_id = root_id
    visited = set()

    while current_id and current_id not in visited:
        visited.add(current_id)
        node = mapping.get(current_id)
        if not node:
            break

        msg = node.get("message")
        if msg and msg.get("author", {}).get("role") in ("user", "assistant"):
            # Extract text content from parts
            content = msg.get("content", {})
            parts = content.get("parts", [])
            text_parts = []
            for part in parts:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict):
                    # Image or file reference — note it but skip content
                    ct = part.get("content_type", "attachment")
                    text_parts.append(f"[{ct}]")
            text = "\n".join(text_parts).strip()

            if text:
                messages.append({
                    "role": msg["author"]["role"],
                    "content": text,
                    "create_time": msg.get("create_time"),
                })

        # Follow children — prefer active branch (weight=1.0)
        children = node.get("children", [])
        if not children:
            break

        # Pick the child whose message has weight=1.0, or first child
        next_id = children[0]
        for child_id in children:
            child_node = mapping.get(child_id, {})
            child_msg = child_node.get("message")
            if child_msg and child_msg.get("weight", 1.0) == 1.0:
                next_id = child_id
                break
        current_id = next_id

    return messages


def _summarize_conversation(messages: list[dict], max_chars: int = 2000) -> str:
    """Summarize a conversation into a compact string."""
    lines = []
    chars = 0
    for msg in messages:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"][:300]
        line = f"**{role}**: {content}"
        if chars + len(line) > max_chars:
            lines.append("... (truncated)")
            break
        lines.append(line)
        chars += len(line) + 1
    return "\n\n".join(lines)


async def import_chatgpt(
    mem,
    file: Path,
    agent_id: str = "chatgpt",
    min_messages: int = 4,
    compress: bool = False,
) -> ImportResult:
    """Import ChatGPT conversations.json as journal entries.

    Each conversation with >= min_messages exchanges becomes a journal entry.
    Short conversations (greetings, quick questions) are skipped.

    Args:
        mem: Initialized MemorySystem.
        file: Path to conversations.json.
        agent_id: Agent ID for imported entries.
        min_messages: Minimum messages for a conversation to be imported.
        compress: Whether to LLM-compress content.

    Returns:
        ImportResult with counts.
    """
    result = ImportResult()

    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        result.errors += 1
        result.details.append(f"Failed to parse {file}: {e}")
        return result

    if not isinstance(data, list):
        result.errors += 1
        result.details.append(f"Expected JSON array, got {type(data).__name__}")
        return result

    for conv in tqdm(data, desc="Importing", unit="conv"):
        try:
            title = conv.get("title", "Untitled conversation")
            mapping = conv.get("mapping", {})
            create_time = conv.get("create_time")
            model = conv.get("default_model_slug", "unknown")

            if not mapping:
                result.skipped += 1
                continue

            # Walk the active branch to get linear conversation
            messages = _walk_active_branch(mapping)

            if len(messages) < min_messages:
                result.skipped += 1
                continue

            # Build content summary
            content = _summarize_conversation(messages)
            if not content:
                result.skipped += 1
                continue

            # Determine importance based on conversation length
            msg_count = len(messages)
            if msg_count >= 20:
                importance = 7  # Long, substantive conversation
            elif msg_count >= 10:
                importance = 6
            else:
                importance = 5

            # Format timestamp
            ts = ""
            if create_time:
                try:
                    dt = datetime.fromtimestamp(create_time)
                    ts = dt.strftime("%Y-%m-%d")
                except (ValueError, OSError):
                    pass

            await mem.journal.add(
                agent_id=agent_id,
                entry_type="conversation",
                title=f"{title}" + (f" ({ts})" if ts else ""),
                content=f"Model: {model} | Messages: {msg_count}\n\n{content}",
                importance=importance,
                tags=["chatgpt", model],
                compress=compress,
            )
            result.imported += 1

        except Exception as e:
            result.errors += 1
            result.details.append(f"Error on conversation '{conv.get('title', '?')}': {e}")

    return result
