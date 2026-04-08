"""
Anthropic Claude LLM provider.
"""

from __future__ import annotations

import json
import logging

from nmem.exceptions import LLMError

logger = logging.getLogger(__name__)


class AnthropicLLMProvider:
    """LLM provider using the Anthropic Python SDK.

    Args:
        model: Model ID (e.g., "claude-sonnet-4-20250514").
        api_key: Anthropic API key.
    """

    def __init__(self, model: str, api_key: str | None = None):
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            raise ImportError(
                "anthropic package required for Anthropic LLM provider. "
                "Install with: pip install nmem[anthropic]"
            )

        self._model = model
        self._client = AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.2,
        timeout: float = 30.0,
    ) -> str:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text if response.content else ""
        except Exception as e:
            raise LLMError(f"Anthropic completion failed: {e}") from e

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        timeout: float = 30.0,
    ) -> dict | list | None:
        text = await self.complete(
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
        if not text:
            return None
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines[1:] if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.debug("Failed to parse JSON from Anthropic response: %s", text[:200])
            return None
