import hashlib
from typing import Any, Dict, List, Optional, Set, Union

from ..llm import GeminiClient
from ..models import (
    BatchPlan,
    BatchSelection,
    Element,
    ExtractedOption,
    ExtractedQuestion,
    PageState,
    QuestionExtractionResult,
    ValidatedAnswerAction,
    ValidatedAnswerPlan,
)


class AnswerPlanningAgent:
    """Agent E: Token-efficient LLM answer planner with strict call ceiling, deduplication, and option validation."""

    def __init__(self, llm: GeminiClient, max_llm_calls: int = 5):
        self.llm = llm
        self.max_llm_calls = max_llm_calls
        self.calls_made = 0
        self._cache: Dict[str, BatchPlan] = {}

    @staticmethod
    def _fingerprint_questions(questions: List[ExtractedQuestion]) -> str:
        parts = [
            f"{q.question_number}:{q.prompt}:" + ",".join(f"{o.element_id}={o.text}" for o in q.options)
            for q in questions
        ]
        return hashlib.sha1("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()[:16]

    async def plan_answers(
        self,
        state: Union[PageState, Dict[str, Any]],
        extraction: QuestionExtractionResult,
        user_data: Optional[Dict[str, Any]] = None,
    ) -> ValidatedAnswerPlan:
        questions = extraction.questions
        if not questions:
            return ValidatedAnswerPlan(
                actions=[],
                total_questions=0,
                validation_errors=["Cannot plan answers: 0 extracted questions."],
            )

        fp = self._fingerprint_questions(questions)
        if fp in self._cache:
            raw_plan = self._cache[fp]
        else:
            if self.calls_made >= self.max_llm_calls:
                return ValidatedAnswerPlan(
                    actions=[],
                    total_questions=len(questions),
                    unplanned_question_ids=[q.question_id for q in questions],
                    validation_errors=[f"LLM call ceiling ({self.max_llm_calls}) reached."],
                )

            self.calls_made += 1
            state_dict = (
                state
                if isinstance(state, dict)
                else {
                    "url": state.url,
                    "title": state.title,
                    "text": state.text,
                    "elements": [el.model_dump() for el in state.elements],
                    "screenshot_path": state.screenshot_path,
                }
            )

            # Support both updated GeminiClient (accepts extracted_questions) and mocked test LLMs
            try:
                raw_plan = await self.llm.plan_test(
                    state_dict,
                    user_data,
                    extracted_questions=questions,
                )
            except TypeError:
                raw_plan = await self.llm.plan_test(state_dict, user_data)

            self._cache[fp] = raw_plan

        return self.validate_plan(raw_plan, extraction, state)

    def validate_plan(
        self,
        raw_plan: Optional[BatchPlan],
        extraction: QuestionExtractionResult,
        state: Union[PageState, Dict[str, Any]],
    ) -> ValidatedAnswerPlan:
        questions = extraction.questions
        if not raw_plan or not raw_plan.selections or int(raw_plan.total_questions or 0) <= 0:
            return ValidatedAnswerPlan(
                actions=[],
                total_questions=0,
                unplanned_question_ids=[q.question_id for q in questions],
                validation_errors=["Gemini returned an empty or zero-question plan."],
                raw_plan=raw_plan,
            )

        ps = PageState.from_any(state)
        all_elements_by_id: Dict[int, Element] = {el.id: el for el in ps.elements}

        # Map each element_id to its (ExtractedQuestion, ExtractedOption)
        option_by_element_id: Dict[int, tuple[ExtractedQuestion, ExtractedOption]] = {}
        question_by_number: Dict[int, ExtractedQuestion] = {}
        for q in questions:
            question_by_number[q.question_number] = q
            for opt in q.options:
                option_by_element_id[opt.element_id] = (q, opt)

        actions: List[ValidatedAnswerAction] = []
        errors: List[str] = []
        covered_question_ids: Set[str] = set()

        for sel in raw_plan.selections:
            matched_pair = option_by_element_id.get(sel.element_id)
            if matched_pair is not None:
                q, opt = matched_pair
                # For <select> dropdowns, resolve the chosen option value/text
                chosen_value = sel.option_value or opt.value
                chosen_text = opt.text
                if q.question_type == "select_dropdown" and sel.option_value:
                    for candidate_opt in q.options:
                        if candidate_opt.value == sel.option_value or candidate_opt.text == sel.option_value:
                            chosen_value = candidate_opt.value
                            chosen_text = candidate_opt.text
                            break

                actions.append(
                    ValidatedAnswerAction(
                        question_id=q.question_id,
                        question_number=q.question_number,
                        question_prompt=q.prompt,
                        element_id=sel.element_id,
                        option_text=chosen_text,
                        option_value=chosen_value,
                        control_type=opt.control_type,
                        locator_hint=opt.locator_hint,
                        confidence=getattr(sel, "confidence", 0.9) or 0.9,
                        reason=sel.reason or "",
                    )
                )
                covered_question_ids.add(q.question_id)
            elif sel.element_id in all_elements_by_id:
                # Fallback when unit test provides a minimal element not grouped into options
                el = all_elements_by_id[sel.element_id]
                q_num = sel.question_number or (len(actions) + 1)
                q_id = f"q_{q_num}"
                opt_label = (el.text or el.aria_label or el.value or el.name or "обраний елемент").strip()
                q_prompt = (el.question_heading or el.context or f"Питання #{q_num}").strip()
                actions.append(
                    ValidatedAnswerAction(
                        question_id=q_id,
                        question_number=q_num,
                        question_prompt=q_prompt,
                        element_id=el.id,
                        option_text=opt_label,
                        option_value=sel.option_value or el.value,
                        control_type=(el.type or el.tag or "radio").lower(),
                        locator_hint={
                            "tag": el.tag,
                            "type": el.type,
                            "name": el.name,
                            "value": el.value,
                            "text": opt_label[:120],
                            "html_id": el.html_id,
                        },
                        confidence=getattr(sel, "confidence", 0.9) or 0.9,
                        reason=sel.reason or "",
                    )
                )
                covered_question_ids.add(q_id)
            else:
                msg = f"WARNING: Gemini returned an unknown answer element: {sel.element_id}"
                print(msg)
                errors.append(msg)

        unplanned = [q.question_id for q in questions if q.question_id not in covered_question_ids]
        reported_total = max(int(raw_plan.total_questions or 0), len(questions))

        return ValidatedAnswerPlan(
            actions=actions,
            total_questions=reported_total,
            unplanned_question_ids=unplanned,
            validation_errors=errors,
            raw_plan=raw_plan,
        )
