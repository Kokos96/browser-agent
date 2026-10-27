import re
from typing import Any, Dict, List, Optional, Sequence, Union

from ..browser import BrowserController
from ..models import Element, PageState, SubmissionResult
from .question_extractor import QuizQuestionExtractor


class SubmissionAndResultAgent:
    """Agent G: Handles final submission, result verification, and explicit score extraction."""

    SCORE_PATTERNS = (
        r"(?:ваш результат(?: тесту)?|результат тесту|your score|test score|you scored|"
        r"ви набрали|вы набрали|score)\s*[:：—-]?\s*"
        r"(\d+)\s*(?:/|із|з|из|out of|of)\s*(\d+)",
        r"(?:правильних відповідей|правильні відповіді|правильных ответов|"
        r"верных ответов|correct answers)\s*[:：—-]?\s*"
        r"(\d+)\s*(?:/|із|з|из|out of|of)\s*(\d+)",
        r"(\d+)\s+(?:із|з|из|out of|of)\s+(\d+)\s+"
        r"(?:правильних відповідей|правильных ответов|correct answers)",
    )

    @classmethod
    def extract_score(cls, result_text: str) -> Optional[str]:
        """Extract an explicit score or correct-answer count; never infer or fabricate one."""
        for pattern in cls.SCORE_PATTERNS:
            match = re.search(pattern, result_text or "", flags=re.IGNORECASE)
            if match:
                return f"{match.group(1)}/{match.group(2)}"
        return None

    @staticmethod
    def find_submit_button(
        state_or_elements: Union[PageState, Dict[str, Any], Sequence[Union[Element, Dict[str, Any]]]]
    ) -> Optional[int]:
        if isinstance(state_or_elements, PageState):
            elements = state_or_elements.elements
        elif isinstance(state_or_elements, dict):
            elements = state_or_elements.get("elements", [])
        else:
            elements = state_or_elements
        return QuizQuestionExtractor.find_submit_control(elements)

    async def submit_and_verify(
        self,
        browser: BrowserController,
        state: Union[PageState, Dict[str, Any]],
        auto_submit: bool = True,
        wait_ms: int = 1500,
    ) -> SubmissionResult:
        ps = PageState.from_any(state)
        submit_id = self.find_submit_button(ps)

        if submit_id is None:
            existing_score = self.extract_score(ps.text)
            if existing_score:
                print("Результат тесту:", existing_score)
                return SubmissionResult(
                    submission_attempted=False,
                    submission_confirmed=True,
                    submit_control_id=None,
                    submit_control_not_found=False,
                    result_page_captured=True,
                    score_captured=True,
                    score_text=existing_score,
                    test_outcome="score_captured",
                    result_excerpt=ps.text[:3000],
                    result_url=ps.url,
                    result_title=ps.title,
                    evidence=["score_present_without_submit_button"],
                )

            return SubmissionResult(
                submission_attempted=False,
                submission_confirmed=False,
                submit_control_id=None,
                submit_control_not_found=True,
                test_outcome="submit_control_not_found",
                result_url=ps.url,
                result_title=ps.title,
                evidence=["submit_button_missing_and_no_score_on_page"],
            )

        print("Final button found:", submit_id)

        if not auto_submit:
            print("AUTO_SUBMIT=false")
            print("Test answers are selected. Submit manually.")
            return SubmissionResult(
                submission_attempted=False,
                submission_confirmed=False,
                submit_control_id=submit_id,
                awaiting_manual_submission=True,
                test_outcome="awaiting_manual_submission",
                result_url=ps.url,
                result_title=ps.title,
                evidence=["auto_submit_disabled"],
            )

        print("Browser: submitting test...")
        await browser.click(submit_id)
        await browser.wait(wait_ms)
        print("Browser: test submitted.")

        try:
            result_state = await browser.inspect()
            result_ps = PageState.from_any(result_state)
            result_text = result_ps.text
            excerpt = result_text[:3000]

            print("Result page URL:", result_ps.url)
            print("Result page title:", result_ps.title)
            print("Result page text:")
            print(excerpt)

            score = self.extract_score(result_text)
            if score:
                print("Результат тесту:", score)
                return SubmissionResult(
                    submission_attempted=True,
                    submission_confirmed=True,
                    submit_control_id=submit_id,
                    result_page_captured=True,
                    score_captured=True,
                    score_text=score,
                    test_outcome="score_captured",
                    result_excerpt=excerpt,
                    result_url=result_ps.url,
                    result_title=result_ps.title,
                    evidence=["submit_clicked", f"score_extracted:{score}"],
                )

            extracted = QuizQuestionExtractor().extract(result_ps)
            still_has_quiz_questions = bool(extracted.questions)
            combined_result_text = (
                f"{result_ps.title}\n{result_ps.url}\n{result_text}"
            ).lower()
            result_indicators = (
                "quiz completed",
                "test completed",
                "test submitted",
                "submission confirmed",
                "your results",
                "test results",
                "quiz results",
                "дякуємо за проходження",
                "тест завершено",
                "тест пройдено",
                "результати тесту",
                "результат тесту",
                "правильних відповідей",
                "правильных ответов",
                "тест завершен",
                "тест пройден",
            )
            has_result_indicator = any(
                marker in combined_result_text for marker in result_indicators
            )
            location_changed = (
                result_ps.url != ps.url or result_ps.title != ps.title
            )
            visible_content_changed = result_text.strip() != ps.text.strip()

            result_transition_confirmed = (
                not still_has_quiz_questions
                and (
                    has_result_indicator
                    or location_changed
                    or visible_content_changed
                )
            )
            if not result_transition_confirmed:
                message = (
                    "Кнопку надсилання натиснуто, але після очікування не знайдено "
                    "підтвердження завершення тесту або переходу до результату."
                )
                print(message)
                return SubmissionResult(
                    submission_attempted=True,
                    submission_confirmed=False,
                    submit_control_id=submit_id,
                    result_page_captured=False,
                    score_captured=False,
                    score_text=None,
                    test_outcome="submission_unconfirmed",
                    result_excerpt=excerpt,
                    result_url=result_ps.url,
                    result_title=result_ps.title,
                    evidence=["submit_clicked", "no_observable_result_transition"],
                )

            print("Сторінку результату змінено, але явний бал або кількість правильних відповідей не знайдено.")
            return SubmissionResult(
                submission_attempted=True,
                submission_confirmed=True,
                submit_control_id=submit_id,
                result_page_captured=True,
                score_captured=False,
                score_text=None,
                test_outcome="result_page_captured",
                result_excerpt=excerpt,
                result_url=result_ps.url,
                result_title=result_ps.title,
                evidence=["submit_clicked", "result_transition_confirmed_without_explicit_score"],
            )
        except Exception as error:
            print("Result page capture failed:", error)
            return SubmissionResult(
                submission_attempted=True,
                submission_confirmed=False,
                submit_control_id=submit_id,
                result_page_captured=False,
                score_captured=False,
                test_outcome="result_capture_failed",
                evidence=["submit_clicked", f"result_capture_error:{error}"],
            )
