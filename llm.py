import asyncio
import re

from google import genai
from google.genai import types

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

        contents = [
            SYSTEM_PROMPT,
            prompt
        ]

        screenshot_path = page_state.get(
            "screenshot_path"
        )

        if (
            settings.enable_vision
            and screenshot_path
        ):
            try:
                with open(
                    screenshot_path,
                    "rb"
                ) as image_file:

                    image_bytes = image_file.read()

                contents.append(
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type="image/png"
                    )
                )

            except Exception as error:
                print(
                    "Vision input could not be attached:",
                    error
                )

        try:
            print(
                "Gemini request..."
            )

            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": (
                        AgentAction.model_json_schema()
                    ),
                }
            )

            return AgentAction.model_validate_json(
                response.text
            )

        except Exception as error:
            error_text = str(error)

            print(
                "Gemini error:",
                error_text
            )

            retry_seconds = self._extract_retry_delay(
                error_text
            )

            if retry_seconds is not None:
                print(
                    f"Gemini requested retry after "
                    f"{retry_seconds} seconds."
                )

                await asyncio.sleep(
                    retry_seconds
                )

                try:
                    response = (
                        await self.client.aio.models.generate_content(
                            model=self.model,
                            contents=contents,
                            config={
                                "response_mime_type": "application/json",
                                "response_schema": (
                                    AgentAction.model_json_schema()
                                ),
                            }
                        )
                    )

                    return AgentAction.model_validate_json(
                        response.text
                    )

                except Exception as retry_error:
                    raise RuntimeError(
                        "Gemini request failed after "
                        "server-requested retry."
                    ) from retry_error

            raise RuntimeError(
                "Gemini request failed."
            ) from error

    @staticmethod
    def _extract_retry_delay(
        error_text: str
    ) -> int | None:

        match = re.search(
            r"retryDelay['\"]?\s*:\s*['\"]?(\d+)s",
            error_text
        )

        if match:
            return int(match.group(1)) + 2

        match = re.search(
            r"retry in\s+(\d+)\s*seconds?",
            error_text,
            re.IGNORECASE
        )

        if match:
            return int(match.group(1)) + 2

        if "429" in error_text:
            return 65

        return None