"""
Write-time LLM compression.

Distills verbose content into dense factual statements using a small LLM.
Falls back to truncation when LLM is unavailable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nmem.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


async def compress_content(
    llm: LLMProvider,
    title: str,
    content: str,
    max_chars: int = 200,
    max_tokens: int = 128,
) -> str:
    """Compress content using LLM distillation.

    Preserves names, dates, numbers, and decisions.
    Falls back to truncation on LLM failure.

    Args:
        llm: LLM provider instance.
        title: Content title (provides context).
        content: Raw content to compress.
        max_chars: Maximum output characters.
        max_tokens: Maximum LLM tokens.

    Returns:
        Compressed content string.
    """
    if len(content) <= max_chars:
        return content

    system = (
        f"Distill the following into a single factual statement. "
        f"Keep names, dates, numbers, and decisions. "
        f"Max {max_chars} characters. Output ONLY the compressed fact, nothing else."
    )
    user = f"{title}: {content[:1000]}"

    try:
        result = await llm.complete(
            system, user,
            max_tokens=max_tokens,
            temperature=0.1,
            timeout=10.0,
        )
        compressed = result.strip()
        if compressed and len(compressed) <= max_chars * 2:
            return compressed[:max_chars]
    except Exception as e:
        logger.debug("Compression failed (falling back to truncation): %s", e)

    return f"{title}: {content[:max_chars]}"


async def merge_duplicates(
    llm: LLMProvider,
    entries: list[dict],
    max_chars: int = 300,
) -> str | None:
    """Merge duplicate entries into a single synthesized entry.

    Args:
        llm: LLM provider instance.
        entries: List of dicts with "title" and "content" keys.
        max_chars: Maximum output characters.

    Returns:
        Merged content string, or None if LLM unavailable.
    """
    if not entries:
        return None

    combined = "\n".join(
        f"- {e.get('title', '')}: {e.get('content', '')[:200]}" for e in entries
    )

    system = (
        f"These are duplicate/overlapping memory entries. Merge them into a single, "
        f"definitive factual statement. Preserve all unique information. "
        f"Max {max_chars} characters. Output ONLY the merged fact."
    )

    try:
        result = await llm.complete(
            system, combined,
            max_tokens=256,
            temperature=0.2,
            timeout=15.0,
        )
        merged = result.strip()
        if merged:
            return merged[:max_chars]
    except Exception as e:
        logger.debug("Merge failed: %s", e)

    return None
