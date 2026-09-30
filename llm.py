from google import genai
from google.genai import types

from config import settings
from models import BatchPlan
from prompts import (
    SYSTEM_PROMPT,
    build_batch_prompt
)


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

    async def plan_test(
        self,
        page_state: dict,
        user_data: dict
    ) -> BatchPlan:

        prompt = build_batch_prompt(
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

                    image_bytes = (
                        image_file.read()
                    )

                contents.append(
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type="image/png"
                    )
                )

            except Exception as error:

                print(
                    "Vision error:",
                    error
                )

        print()
        print(
            "Gemini: generating COMPLETE test plan..."
        )

        print(
            "Model:",
            self.model
        )

        try:

            response = (
                await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=BatchPlan,
                        temperature=0.1,
                    )
                )
            )

        except Exception as error:

            error_text = str(error)

            print(
                "Gemini error:",
                error_text
            )

            if "429" in error_text:

                raise RuntimeError(
                    "Gemini RPD/RPM quota exceeded. "
                    "The agent intentionally does not retry."
                ) from error

            raise RuntimeError(
                "Gemini request failed."
            ) from error

        if not response.text:

            raise RuntimeError(
                "Gemini returned an empty response."
            )

        try:

            plan = BatchPlan.model_validate_json(
                response.text
            )

        except Exception as error:

            print(
                "Invalid Gemini response:"
            )

            print(
                response.text
            )

            raise RuntimeError(
                "Gemini returned invalid structured output."
            ) from error

        print()
        print(
            "Gemini plan received."
        )

        print(
            "Questions:",
            plan.total_questions
        )

        print(
            "Selections:",
            len(plan.selections)
        )

        return plan