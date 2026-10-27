import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Union

from ..models import (
    Element,
    ExtractedOption,
    ExtractedQuestion,
    PageState,
    QuestionExtractionResult,
)
from .navigation_agent import NavigationAndLaunchAgent


class QuizQuestionExtractor:
    """Agent D: Extracts structured quiz questions, options, and navigation/submit controls."""

    SUBMIT_PHRASES = (
        "завершити тест",
        "завершити опитування",
        "завершити",
        "відправити відповіді",
        "відправити",
        "закінчити тест",
        "здати тест",
        "завершить тест",
        "отправить ответы",
        "отправить",
        "закончить тест",
        "submit quiz",
        "submit test",
        "submit answers",
        "submit",
        "finish test",
        "finish quiz",
        "finish",
    )

    NEXT_PHRASES = (
        "наступне питання",
        "наступний",
        "далі",
        "вперед",
        "следующий вопрос",
        "далее",
        "дальше",
        "next question",
        "next",
        "continue",
    )

    NON_OPTION_PHRASES = (
        "поділитися",
        "поделиться",
        "share",
        "restart",
        "retry",
        "спочатку",
        "заново",
        "cookie",
        "privacy",
        "terms",
        "login",
        "sign in",
        "sign up",
        "register",
        "subscribe",
        "home",
        "головна",
        "главная",
        "меню",
        "menu",
    )

    PROFILE_FIELD_NAMES = {"surname", "name", "grp", "group", "email", "phone", "username", "password"}

    @classmethod
    def find_submit_control(cls, elements: Sequence[Union[Element, Dict[str, Any]]]) -> Optional[int]:
        for raw in elements:
            el = Element.from_any(raw)
            if el.disabled or not el.visible or el.in_nav_or_footer:
                continue
            tag = (el.tag or "").lower()
            role = (el.role or "").lower()
            el_type = (el.type or "").lower()
            if tag not in {"button", "input", "a"} and role != "button":
                continue
            if tag == "input" and el_type not in {"submit", "button"}:
                continue
            text = re.sub(
                r"\s+",
                " ",
                " ".join(str(x or "") for x in (el.text, el.aria_label, el.title, el.value)),
            ).strip().lower()
            if not text:
                continue
            if any(phrase in text for phrase in cls.SUBMIT_PHRASES):
                return el.id
        return None

    @classmethod
    def find_next_control(
        cls,
        elements: Sequence[Union[Element, Dict[str, Any]]],
        submit_id: Optional[int] = None,
    ) -> Optional[int]:
        for raw in elements:
            el = Element.from_any(raw)
            if el.id == submit_id or el.disabled or not el.visible or el.in_nav_or_footer:
                continue
            tag = (el.tag or "").lower()
            role = (el.role or "").lower()
            el_type = (el.type or "").lower()
            if tag not in {"button", "input", "a"} and role != "button":
                continue
            if tag == "input" and el_type not in {"button", "submit"}:
                continue
            text = re.sub(
                r"\s+",
                " ",
                " ".join(str(x or "") for x in (el.text, el.aria_label, el.title, el.value)),
            ).strip().lower()
            if not text:
                continue
            if any(
                re.search(rf"\b{re.escape(phrase)}\b", text)
                for phrase in cls.NEXT_PHRASES
            ):
                return el.id
        return None

    def extract(self, state: Union[PageState, Dict[str, Any]]) -> QuestionExtractionResult:
        ps = PageState.from_any(state)
        elements = ps.elements

        submit_id = self.find_submit_control(elements)
        next_id = self.find_next_control(elements, submit_id=submit_id)
        start_ids = {c.element_id for c in NavigationAndLaunchAgent.rank_start_controls(elements)}
        excluded_ids = {cid for cid in (submit_id, next_id) if cid is not None} | start_ids

        questions: List[ExtractedQuestion] = []
        notes: List[str] = []

        # 1. Extract radio / checkbox / ARIA radio/checkbox/option groups
        choice_groups: "OrderedDict[str, List[Element]]" = OrderedDict()
        for el in elements:
            if el.id in excluded_ids or el.disabled or not el.visible or el.in_nav_or_footer:
                continue
            tag = (el.tag or "").lower()
            role = (el.role or "").lower()
            el_type = (el.type or "").lower()

            is_choice = (
                (tag == "input" and el_type in {"radio", "checkbox"})
                or role in {"radio", "checkbox", "option"}
            )
            if not is_choice:
                continue

            group_key = (
                (f"name:{el.name}" if el.name else "")
                or (f"cont:{el.container_id}" if el.container_id else "")
                or (f"head:{el.question_heading[:120]}" if el.question_heading else "")
                or (f"ctx:{el.context[:120]}" if el.context else "")
                or f"choice_group_{len(choice_groups) or 1}"
            )
            choice_groups.setdefault(group_key, []).append(el)

        for group_key, group_elems in choice_groups.items():
            # Allow 1 radio/checkbox in unit test fixtures, or >= 2 options in real pages
            is_native_choice = any((e.type or "").lower() in {"radio", "checkbox"} for e in group_elems)
            if len(group_elems) < 2 and not is_native_choice:
                continue

            first = group_elems[0]
            prompt = self._derive_question_prompt(group_elems, len(questions) + 1)
            q_type = (
                "multiple_choice"
                if any((e.type or "").lower() == "checkbox" or (e.role or "").lower() == "checkbox" for e in group_elems)
                else "single_choice"
            )
            options: List[ExtractedOption] = []
            for idx, el in enumerate(group_elems, start=1):
                opt_text = (el.text or el.aria_label or el.value or el.name or f"Option {idx}").strip()
                ctrl_type = (el.type or el.role or "radio").lower()
                options.append(
                    ExtractedOption(
                        option_id=f"q{len(questions)+1}_opt{idx}",
                        element_id=el.id,
                        text=opt_text[:300],
                        value=el.value,
                        control_type=ctrl_type,
                        checked=bool(el.checked),
                        locator_hint={
                            "tag": el.tag,
                            "type": el.type,
                            "name": el.name,
                            "value": el.value,
                            "text": opt_text[:120],
                            "html_id": el.html_id,
                            "container_id": el.container_id,
                        },
                    )
                )

            questions.append(
                ExtractedQuestion(
                    question_id=f"q_{len(questions)+1}",
                    question_number=len(questions) + 1,
                    prompt=prompt,
                    question_type=q_type,
                    container_signature=group_key,
                    options=options,
                    answered=any(o.checked for o in options),
                )
            )

        # 2. Extract <select> dropdown questions
        for el in elements:
            if el.id in excluded_ids or el.disabled or not el.visible or el.in_nav_or_footer:
                continue
            if (el.tag or "").lower() != "select":
                continue
            if (el.name or "").lower() in self.PROFILE_FIELD_NAMES:
                continue

            options: List[ExtractedOption] = []
            raw_opts = el.select_options or []
            for idx, opt_dict in enumerate(raw_opts, start=1):
                opt_val = str(opt_dict.get("value") or "")
                opt_txt = str(opt_dict.get("text") or "").strip()
                if not opt_txt or opt_txt.lower() in {"choose...", "select...", "оберіть...", "выберите..."}:
                    continue
                options.append(
                    ExtractedOption(
                        option_id=f"q{len(questions)+1}_opt{idx}",
                        element_id=el.id,
                        text=opt_txt[:300],
                        value=opt_val or opt_txt,
                        control_type="select",
                        checked=(el.value == opt_val and bool(opt_val)),
                        locator_hint={
                            "tag": "select",
                            "name": el.name,
                            "html_id": el.html_id,
                            "option_value": opt_val or opt_txt,
                            "text": opt_txt[:120],
                        },
                    )
                )

            if options:
                prompt = el.question_heading or el.context or el.aria_label or f"Питання #{len(questions)+1}"
                questions.append(
                    ExtractedQuestion(
                        question_id=f"q_{len(questions)+1}",
                        question_number=len(questions) + 1,
                        prompt=prompt[:400],
                        question_type="select_dropdown",
                        container_signature=f"select:{el.id}",
                        options=options,
                        answered=any(o.checked for o in options),
                    )
                )

        # 3. Extract custom button/card-based quiz options when no native radio/checkbox/select questions were found
        if not questions:
            button_groups: "OrderedDict[str, List[Element]]" = OrderedDict()
            for el in elements:
                if el.id in excluded_ids or el.disabled or not el.visible or el.in_nav_or_footer:
                    continue
                tag = (el.tag or "").lower()
                role = (el.role or "").lower()
                if tag != "button" and role != "button":
                    continue

                btn_text = (el.text or el.aria_label or "").strip()
                if not btn_text or len(btn_text) > 250:
                    continue
                lower_btn = btn_text.lower()
                if any(bad in lower_btn for bad in self.NON_OPTION_PHRASES):
                    continue

                # Require either a detected question container/heading or question-like context
                has_question_signal = (
                    bool(el.container_id)
                    or "?" in (el.question_heading or "")
                    or "?" in (el.context or "")
                    or bool(re.search(r"(?:питання|вопрос|question)\s*\d*", (el.context or "").lower()))
                )
                if not has_question_signal:
                    continue

                g_key = el.container_id or el.question_heading[:120] or el.context[:120]
                if not g_key:
                    continue
                button_groups.setdefault(g_key, []).append(el)

            for g_key, btn_elems in button_groups.items():
                if len(btn_elems) < 2 or len(btn_elems) > 10:
                    continue
                prompt = self._derive_question_prompt(btn_elems, len(questions) + 1)
                options = [
                    ExtractedOption(
                        option_id=f"q{len(questions)+1}_opt{idx}",
                        element_id=el.id,
                        text=(el.text or el.aria_label or f"Option {idx}").strip()[:300],
                        value=el.value,
                        control_type="button",
                        checked=bool(el.selected),
                        locator_hint={
                            "tag": el.tag,
                            "role": el.role,
                            "text": (el.text or "")[:120],
                            "html_id": el.html_id,
                            "container_id": el.container_id,
                        },
                    )
                    for idx, el in enumerate(btn_elems, start=1)
                ]
                questions.append(
                    ExtractedQuestion(
                        question_id=f"q_{len(questions)+1}",
                        question_number=len(questions) + 1,
                        prompt=prompt,
                        question_type="single_choice",
                        container_signature=f"btn_group:{g_key}",
                        options=options,
                        answered=any(o.checked for o in options),
                    )
                )

        if not questions:
            notes.append("No structured quiz questions detected on current DOM.")

        return QuestionExtractionResult(
            questions=questions,
            submit_control_id=submit_id,
            next_control_id=next_id,
            page_readiness_notes=notes,
        )

    @staticmethod
    def _derive_question_prompt(group_elems: List[Element], fallback_number: int) -> str:
        for el in group_elems:
            if el.question_heading and len(el.question_heading.strip()) >= 3:
                return el.question_heading.strip()[:400]
        for el in group_elems:
            if el.context and len(el.context.strip()) >= 3:
                ctx = el.context.strip()
                # Strip option texts from context if possible to keep prompt clean
                for opt_el in group_elems:
                    if opt_el.text and len(opt_el.text) >= 2 and opt_el.text in ctx:
                        ctx = ctx.replace(opt_el.text, " ").strip()
                ctx = re.sub(r"\s+", " ", ctx).strip()
                if len(ctx) >= 3:
                    return ctx[:400]
                return el.context.strip()[:400]
        return f"Питання #{fallback_number}"
