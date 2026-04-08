"""
LLM provider protocol.

nmem uses an LLM for three operations:
  1. Content compression — distill verbose text to dense facts
  2. Nightly synthesis — extract cross-cutting patterns from journal entries
  3. Deduplication merging — combine similar entries into one

All share the same calling pattern: system prompt + user prompt -> text response.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM text generation backends."""

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.2,
        timeout: float = 30.0,
    ) -> str:
        """Generate a text completion.

        Args:
            system_prompt: System-level instructions.
            user_prompt: User-level input.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            timeout: Request timeout in seconds.

        Returns:
            The generated text response.
        """
        ...

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        timeout: float = 30.0,
    ) -> dict | list | None:
        """Generate and parse a JSON response.

        Args:
            system_prompt: System-level instructions (should request JSON output).
            user_prompt: User-level input.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            timeout: Request timeout in seconds.

        Returns:
            Parsed JSON (dict or list), or None if parsing fails.
        """
        ...
