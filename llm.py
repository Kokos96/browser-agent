import asyncio

from google import genai

from config import settings
from models import AgentAction
from prompts import SYSTEM_PROMPT, build_prompt


class GeminiClient:

    def __init__(self):
        if not settings.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured."
            )

        self.client = genai.Client(
            api_key=settings.gemini_api_key
        )

        self.model = settings.gemini_model

    async def decide(
        self,
        page_state: dict,
        user_data: dict
    ) -> AgentAction:

        prompt = build_prompt(
            page_state,
            user_data
        )

        last_error = None

        for attempt in range(4):

            try:
                print(
                    f"Gemini request "
                    f"(attempt {attempt + 1}/4)"
                )

                response = (
                    await self.client
                    .aio
                    .models
                    .generate_content(
                        model=self.model,
                        contents=[
                            SYSTEM_PROMPT,
                            prompt
                        ],
                        config={
                            "response_mime_type": (
                                "application/json"
                            ),
                            "response_schema": (
                                AgentAction
                                .model_json_schema()
                            ),
                        }
                    )
                )

                return AgentAction.model_validate_json(
                    response.text
                )

            except Exception as error:

                last_error = error

                print(
                    f"Gemini error: {error}"
                )

                if attempt < 3:

                    delay = 2 ** attempt

                    print(
                        f"Retrying in {delay} seconds..."
                    )

                    await asyncio.sleep(delay)

        raise RuntimeError(
            "Gemini request failed after retries."
        ) from last_error
