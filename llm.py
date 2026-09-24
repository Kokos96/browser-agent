from typing import Any

from config import settings


class GeminiClient:
    """Small integration boundary for the LLM used by the agent loop."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.gemini_api_key
        self.model = settings.gemini_model

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured in the .env file"
            )
        raise NotImplementedError(
            "Add the selected Gemini SDK transport in GeminiClient.generate()"
        )