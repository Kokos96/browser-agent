import os
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from .config import settings
from .models import BatchPlan, ExtractedQuestion
from .prompts import SYSTEM_PROMPT, build_batch_prompt, build_structured_questions_prompt


class GeminiClient:
    """Async Gemini client with token-efficient structured question planning and cumulative usage tracking."""

    def __init__(self, api_key: Optional[str] = None, client: Optional[Any] = None):
        resolved_key = api_key or settings.gemini_api_key
        if not resolved_key and client is None:
            raise RuntimeError("GEMINI_API_KEY is not set")

        self.client = client or genai.Client(api_key=resolved_key)
        self.last_usage: Dict[str, int] = {}
        self.total_usage: Dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 0,
        }
        self.call_count: int = 0

    def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage_metadata", None)
        print()
        print("-" * 60)
        print("GEMINI TOKEN USAGE")
        print("-" * 60)

        self.last_usage = {}
        if usage:
            input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
            output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
            thinking_tokens = int(getattr(usage, "thoughts_token_count", 0) or 0)
            total_tokens = int(getattr(usage, "total_token_count", 0) or 0)

            self.last_usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "thinking_tokens": thinking_tokens,
                "total_tokens": total_tokens,
            }
            for k in self.total_usage:
                self.total_usage[k] += self.last_usage.get(k, 0)

            print(f"Input tokens:    {input_tokens}")
            print(f"Output tokens:   {output_tokens}")
            print(f"Thinking tokens: {thinking_tokens}")
            print(f"Total tokens:    {total_tokens}")
        else:
            print("Token usage metadata is unavailable.")
        print("-" * 60)

    async def plan_structured_questions(
        self,
        url: str,
        title: str,
        questions: List[ExtractedQuestion],
        user_data: Optional[Dict[str, Any]] = None,
        screenshot_path: Optional[str] = None,
    ) -> BatchPlan:
        if not questions:
            return BatchPlan(
                selections=[],
                total_questions=0,
                reason="No extracted questions provided; skipping Gemini call.",
            )

        prompt = build_structured_questions_prompt(
            url=url,
            title=title,
            questions=questions,
            user_data=user_data,
        )
        return await self._generate_plan(prompt, screenshot_path=screenshot_path)

    async def plan_test(
        self,
        state: Dict[str, Any],
        user_data: Optional[Dict[str, Any]] = None,
        extracted_questions: Optional[List[ExtractedQuestion]] = None,
    ) -> BatchPlan:
        if extracted_questions is not None:
            return await self.plan_structured_questions(
                url=str(state.get("url") or ""),
                title=str(state.get("title") or ""),
                questions=extracted_questions,
                user_data=user_data,
                screenshot_path=state.get("screenshot_path"),
            )

        prompt = build_batch_prompt(page_state=state, user_data=user_data)
        return await self._generate_plan(prompt, screenshot_path=state.get("screenshot_path"))

    async def _generate_plan(
        self,
        prompt: str,
        screenshot_path: Optional[str] = None,
    ) -> BatchPlan:
        print()
        print("=" * 60)
        print("Gemini: generating COMPLETE test plan...")
        print("=" * 60)

        contents: List[Any] = [prompt]
        if settings.enable_vision:
            resolved_shot = screenshot_path or "results/test_page.png"
            if os.path.exists(resolved_shot):
                with open(resolved_shot, "rb") as file:
                    image_bytes = file.read()
                contents.append(
                    types.Part.from_bytes(data=image_bytes, mime_type="image/png")
                )

        try:
            self.call_count += 1
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
        except Exception as error:
            error_text = str(error)
            if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
                raise RuntimeError(
                    "Gemini RPD/RPM quota exceeded. The agent intentionally does not retry."
                ) from error
            raise

        self._record_usage(response)

        if not response.text:
            raise RuntimeError("Gemini returned an empty response.")

        try:
            plan = BatchPlan.model_validate_json(response.text)
        except Exception as error:
            print("Gemini raw response:")
            print(response.text)
            raise RuntimeError(f"Failed to parse Gemini BatchPlan: {error}") from error

        print(f"Gemini plan received. Questions planned: {len(plan.selections)} (reported: {plan.total_questions})")
        return plan
