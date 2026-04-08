"""
OpenAI-compatible LLM provider.

Works with OpenAI, vLLM, Ollama, LiteLLM, and any OpenAI-compatible API.
"""

from __future__ import annotations

import json
import logging

from nmem.exceptions import LLMError

logger = logging.getLogger(__name__)


class OpenAICompatibleLLMProvider:
    """LLM provider using the OpenAI Python SDK.

    Works with any OpenAI-compatible API (vLLM, Ollama /v1, LiteLLM, etc.).

    Args:
        model: Model name/ID.
        api_key: API key (use "EMPTY" for local servers).
        base_url: Base URL for the API server.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError(
                "openai package required for OpenAI-compatible LLM provider. "
                "Install with: pip install nmem[openai]"
            )

        self._model = model
        self._client = AsyncOpenAI(
            api_key=api_key or "EMPTY",
            base_url=base_url,
        )

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
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            raise LLMError(f"OpenAI-compatible completion failed: {e}") from e

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
        # Try to extract JSON from response (handle markdown code blocks)
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first and last lines (``` markers)
            lines = [l for l in lines[1:] if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.debug("Failed to parse JSON from LLM response: %s", text[:200])
            return None
