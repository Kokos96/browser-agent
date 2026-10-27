import re
from typing import Any, Callable, Dict, List, Optional, Union

from ..browser import BrowserController
from ..models import (
    BatchExecutionResult,
    Element,
    PageState,
    ProgressEvent,
    ProgressEventType,
    QuestionActionResult,
    ValidatedAnswerAction,
    ValidatedAnswerPlan,
    WorkflowState,
)


class BrowserActionExecutor:
    """Agent F: Resilient DOM action executor with dynamic element re-resolution and selection verification."""

    @staticmethod
    def _clean_log_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @classmethod
    def resolve_target_element_id(
        cls,
        action: ValidatedAnswerAction,
        current_state: Union[PageState, Dict[str, Any]],
    ) -> Optional[int]:
        ps = PageState.from_any(current_state)
        by_id = {el.id: el for el in ps.elements}
        hint = action.locator_hint or {}

        # 1. Check if original element_id still matches the expected signature
        direct = by_id.get(action.element_id)
        if direct is not None and not direct.disabled:
            if cls._matches_hint(direct, hint):
                return direct.id

        # 2. Re-resolve across current DOM elements if indices shifted dynamically
        best_id: Optional[int] = None
        best_score = 0
        for el in ps.elements:
            if el.disabled:
                continue
            score = cls._similarity_score(el, hint, action.option_text)
            if score > best_score:
                best_score = score
                best_id = el.id

        if best_id is not None and best_score >= 3:
            return best_id

        # 3. If no dynamic shift occurred and direct element exists, return direct
        if direct is not None:
            return direct.id

        return None

    @staticmethod
    def _matches_hint(el: Element, hint: Dict[str, Any]) -> bool:
        if not hint:
            return True
        if hint.get("html_id") and el.html_id:
            return el.html_id == hint["html_id"]
        if hint.get("name") and el.name and hint.get("value") and el.value:
            return el.name == hint["name"] and el.value == hint["value"]
        hint_text = (hint.get("text") or "").strip().lower()
        el_text = (el.text or el.aria_label or el.value or "").strip().lower()
        if hint_text and el_text:
            return hint_text in el_text or el_text in hint_text
        return True

    @staticmethod
    def _similarity_score(el: Element, hint: Dict[str, Any], option_text: str) -> int:
        score = 0
        if hint.get("html_id") and el.html_id == hint.get("html_id"):
            score += 6
        if hint.get("name") and el.name == hint.get("name"):
            score += 2
        if hint.get("value") and el.value == hint.get("value"):
            score += 3
        if hint.get("tag") and (el.tag or "").lower() == str(hint.get("tag")).lower():
            score += 1
        target_text = (option_text or hint.get("text") or "").strip().lower()
        el_text = (el.text or el.aria_label or "").strip().lower()
        if target_text and el_text and target_text == el_text:
            score += 4
        elif target_text and el_text and (target_text in el_text or el_text in target_text):
            score += 2
        return score

    async def execute_plan(
        self,
        browser: BrowserController,
        plan: ValidatedAnswerPlan,
        current_state: Union[PageState, Dict[str, Any]],
        on_event: Optional[Callable[[ProgressEvent], None]] = None,
    ) -> BatchExecutionResult:
        results: List[QuestionActionResult] = []
        selected_count = 0
        failed_count = 0
        total_questions = max(plan.total_questions, len(plan.actions))

        for action in plan.actions:
            q_num = action.question_number
            raw_context = self._clean_log_text(action.question_prompt)
            raw_label = self._clean_log_text(action.option_text or "обраний елемент")

            question_context = raw_context[:147].rstrip()
            if len(raw_context) > 150:
                question_context += "…"

            answer_label = raw_label[:97].rstrip()
            if len(raw_label) > 100:
                answer_label += "…"

            description = (
                f"{question_context} — обрано «{answer_label}»"
                if question_context
                else f"обрано «{answer_label}»"
            )

            start_msg = f"Опрацьовую питання {q_num}/{total_questions}: {description}"
            if on_event:
                on_event(
                    ProgressEvent(
                        event_type=ProgressEventType.QUESTION_STARTED,
                        workflow_state=WorkflowState.SELECTING_ANSWERS,
                        message=start_msg,
                        question_index=q_num,
                        total_questions=total_questions,
                        question_text=question_context,
                        selected_option=answer_label,
                        status="in_progress",
                    )
                )
            else:
                print(start_msg)

            target_id = self.resolve_target_element_id(action, current_state)
            if target_id is None:
                err_text = f"Елемент відповіді {action.element_id} не знайдено в DOM"
                fail_msg = f"ERROR: не вдалося вибрати відповідь на питання {q_num}: {err_text}"
                if on_event:
                    on_event(
                        ProgressEvent(
                            event_type=ProgressEventType.ANSWER_SELECTION_FAILED,
                            workflow_state=WorkflowState.SELECTING_ANSWERS,
                            message=fail_msg,
                            question_index=q_num,
                            total_questions=total_questions,
                            question_text=question_context,
                            selected_option=answer_label,
                            status="failed",
                        )
                    )
                else:
                    print(fail_msg)
                failed_count += 1
                results.append(
                    QuestionActionResult(
                        question_id=action.question_id,
                        question_number=q_num,
                        question_prompt=question_context,
                        element_id=action.element_id,
                        selected_option=answer_label,
                        status="failed",
                        verified=False,
                        error=err_text,
                    )
                )
                continue

            try:
                if action.control_type == "select" and action.option_value is not None:
                    await browser.select(target_id, action.option_value)
                else:
                    await browser.select(target_id)

                verified = True
                if hasattr(browser, "verify_selection"):
                    verified = await browser.verify_selection(target_id, action.option_value)

                selected_count += 1
                done_msg = f"Відповідь вибрано: Питання {q_num}/{total_questions}: {description}"
                if on_event:
                    on_event(
                        ProgressEvent(
                            event_type=ProgressEventType.ANSWER_SELECTED,
                            workflow_state=WorkflowState.SELECTING_ANSWERS,
                            message=done_msg,
                            question_index=q_num,
                            total_questions=total_questions,
                            question_text=question_context,
                            selected_option=answer_label,
                            status="selected",
                        )
                    )
                else:
                    print(done_msg)

                results.append(
                    QuestionActionResult(
                        question_id=action.question_id,
                        question_number=q_num,
                        question_prompt=question_context,
                        element_id=target_id,
                        selected_option=answer_label,
                        status="selected",
                        verified=verified,
                    )
                )
            except Exception as error:
                failed_count += 1
                fail_msg = f"ERROR: не вдалося вибрати відповідь на питання {q_num}: {error}"
                if on_event:
                    on_event(
                        ProgressEvent(
                            event_type=ProgressEventType.ANSWER_SELECTION_FAILED,
                            workflow_state=WorkflowState.SELECTING_ANSWERS,
                            message=fail_msg,
                            question_index=q_num,
                            total_questions=total_questions,
                            question_text=question_context,
                            selected_option=answer_label,
                            status="failed",
                        )
                    )
                else:
                    print(fail_msg)

                results.append(
                    QuestionActionResult(
                        question_id=action.question_id,
                        question_number=q_num,
                        question_prompt=question_context,
                        element_id=target_id,
                        selected_option=answer_label,
                        status="failed",
                        verified=False,
                        error=str(error),
                    )
                )

        unanswered = max(0, total_questions - selected_count)
        return BatchExecutionResult(
            results=results,
            answers_selected=selected_count,
            answers_failed=failed_count,
            questions_unanswered=unanswered,
        )
