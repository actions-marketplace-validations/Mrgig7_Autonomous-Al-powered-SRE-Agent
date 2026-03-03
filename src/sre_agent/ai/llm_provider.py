"""LLM provider abstraction with Ollama implementation.

Supports local LLMs via Ollama for code generation.
"""

import logging
from typing import Any, Protocol

import httpx

from sre_agent.config import get_settings

logger = logging.getLogger(__name__)


class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.1,
    ) -> str:
        """Generate text from prompt."""
        ...

    @property
    def model_name(self) -> str:
        """Get the model name."""
        ...


class OllamaProvider:
    """
    Ollama-based local LLM provider.

    Uses Ollama's REST API to generate completions from local models.
    Recommended models: deepseek-coder:6.7b, codellama:7b
    """

    def __init__(
        self,
        model: str = "deepseek-coder:6.7b",
        base_url: str = "http://localhost:11434",
    ):
        """
        Initialize Ollama provider.

        Args:
            model: Ollama model name (e.g., deepseek-coder:6.7b)
            base_url: Ollama server URL
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OllamaProvider":
        """Enter async context."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0, connect=10.0),  # Long timeout for generation
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit async context."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def model_name(self) -> str:
        """Get the model name."""
        return self.model

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.1,
    ) -> str:
        """
        Generate text from prompt.

        Args:
            prompt: Input prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (lower = more deterministic)

        Returns:
            Generated text
        """
        if self._client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context.")

        logger.info(
            "Generating with Ollama",
            extra={"model": self.model, "prompt_length": len(prompt)},
        )

        try:
            response = await self._client.post(
                "/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "num_predict": max_tokens,
                        "temperature": temperature,
                    },
                },
            )
            response.raise_for_status()

            result = response.json()
            generated_text = result.get("response", "")

            logger.info(
                "Ollama generation complete",
                extra={
                    "model": self.model,
                    "response_length": len(generated_text),
                    "eval_count": result.get("eval_count"),
                },
            )

            return generated_text

        except httpx.ConnectError:
            logger.error("Failed to connect to Ollama server")
            raise RuntimeError(
                f"Cannot connect to Ollama at {self.base_url}. "
                "Ensure Ollama is running (ollama serve)."
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama API error: {e.response.status_code}")
            raise RuntimeError(f"Ollama API error: {e.response.text}")

    async def is_available(self) -> bool:
        """Check if Ollama server is available."""
        if self._client is None:
            return False
        try:
            response = await self._client.get("/api/tags")
            return response.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """List available models."""
        if self._client is None:
            return []
        try:
            response = await self._client.get("/api/tags")
            if response.status_code == 200:
                data = response.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            pass
        return []


class MockLLMProvider:
    """
    Mock LLM provider for testing.

    Returns predefined responses for testing purposes.
    """

    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self.default_response = """```diff
--- a/example.py
+++ b/example.py
@@ -1,3 +1,4 @@
 def example():
-    return None
+    if value is None:
+        return []
+    return value
```

This fix adds a null check to handle the case where value is None."""
        self.calls: list[str] = []

    async def __aenter__(self) -> "MockLLMProvider":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    @property
    def model_name(self) -> str:
        return "mock-model"

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.1,
    ) -> str:
        self.calls.append(prompt)

        # Check for matching response
        for key, response in self.responses.items():
            if key in prompt:
                return response

        return self.default_response


def get_llm_provider() -> LLMProvider:
    """
    Get configured LLM provider.

    Returns:
        Configured LLM provider instance
    """
    settings = get_settings()

    # Get provider from settings or environment
    provider_type = getattr(settings, "llm_provider", "ollama")

    if provider_type == "ollama":
        model = getattr(settings, "ollama_model", "deepseek-coder:6.7b")
        base_url = getattr(settings, "ollama_base_url", "http://localhost:11434")
        return OllamaProvider(model=model, base_url=base_url)
    elif provider_type == "mock":
        return MockLLMProvider()
    else:
        raise ValueError(f"Unknown LLM provider: {provider_type}")
