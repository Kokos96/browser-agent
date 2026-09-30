import json
import os

from google import genai
from google.genai import types

from config import settings
from models import BatchPlan
from prompts import SYSTEM_PROMPT, build_batch_prompt


class GeminiClient:
    def __init__(self):
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")

        self.client = genai.Client(
            api_key=settings.gemini_api_key
        )

    async def plan_test(self, state, user_data):
        prompt = build_batch_prompt(
            state=state,
            user_data=user_data,
        )

        print("\n" + "=" * 60)
        print("Gemini: generating COMPLETE test plan...")
        print("=" * 60)

        contents = [prompt]

        # Vision вимкнено за замовчуванням, щоб не витрачати
        # додаткові токени на screenshot.
        if settings.enable_vision:
            screenshot_path = "results/test_page.png"

            if os.path.exists(screenshot_path):
                print("Gemini: attaching screenshot...")
                with open(screenshot_path, "rb") as f:
                    image_bytes = f.read()

                contents.append(
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type="image/png",
                    )
                )

        try:
            response = await self.client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=BatchPlan,
                ),
            )

        except Exception as e:
            error_text = str(e)

            if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
                raise RuntimeError(
                    "Gemini RPD/RPM quota exceeded. "
                    "The agent intentionally does not retry."
                ) from e

            raise

        # ============================================================
        # TOKEN USAGE
        # ============================================================

        usage = getattr(response, "usage_metadata", None)

        print("\n" + "-" * 60)
        print("GEMINI TOKEN USAGE")
        print("-" * 60)

        if usage:
            input_tokens = getattr(
                usage,
                "prompt_token_count",
                0,
            )

            output_tokens = getattr(
                usage,
                "candidates_token_count",
                0,
            )

            thinking_tokens = getattr(
                usage,
                "thoughts_token_count",
                0,
            )

            total_tokens = getattr(
                usage,
                "total_token_count",
                0,
            )

            print(f"Input tokens:    {input_tokens}")
            print(f"Output tokens:   {output_tokens}")
            print(f"Thinking tokens: {thinking_tokens}")
            print(f"Total tokens:    {total_tokens}")

        else:
            print("Token usage metadata is unavailable.")

        print("-" * 60)

        # ============================================================
        # PARSE RESPONSE
        # ============================================================

        if not response.text:
            raise RuntimeError(
                "Gemini returned an empty response."
            )

        try:
            plan = BatchPlan.model_validate_json(
                response.text
            )

        except Exception as e:
            print("\nGemini raw response:")
            print(response.text)

            raise RuntimeError(
                f"Failed to parse Gemini BatchPlan: {e}"
            ) from e

        print("\nGemini plan received.")
        print(
            f"Questions planned: {len(plan.selections)}"
        )

        if plan.total_questions:
            print(
                f"Total questions reported: "
                f"{plan.total_questions}"
            )

        return plan