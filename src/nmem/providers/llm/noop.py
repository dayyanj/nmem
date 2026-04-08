"""
No-op LLM provider — disables LLM-powered features.

When this provider is active:
  - Content compression falls back to truncation
  - Nightly synthesis is skipped
  - Deduplication merging uses simple concatenation
"""

from __future__ import annotations


class NoOpLLMProvider:
    """LLM provider that returns empty results. Use when LLM features are not needed."""

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.2,
        timeout: float = 30.0,
    ) -> str:
        return ""

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        timeout: float = 30.0,
    ) -> dict | list | None:
        return None
