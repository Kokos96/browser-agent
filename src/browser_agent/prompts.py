from typing import Any, Dict, List
from .models import ExtractedQuestion

SYSTEM_PROMPT = """You are the Answer Planning Specialist (Agent E) of a state-aware multi-agent browser automation system.

Your task is to analyze the extracted quiz questions on the current page and return a complete, accurate BatchPlan selecting the best option for every question.

STRICT RULES:
1. Use ONLY element_id values listed in the provided options for each question.
2. Never invent element IDs, questions, or options.
3. Return one selection per single-choice or dropdown question.
4. For <select> dropdown questions, set element_id to the <select> element ID and set option_value to the chosen option value.
5. Never select navigation, share, restart, or submit buttons as quiz answers.
6. Keep reason concise (1 short sentence).
"""


def build_structured_questions_prompt(
    url: str,
    title: str,
    questions: List[ExtractedQuestion],
    user_data: Dict[str, Any] | None = None,
) -> str:
    blocks: List[str] = []
    for q in questions:
        opt_lines: List[str] = []
        for opt in q.options:
            val_part = f" | value={opt.value!r}" if opt.value else ""
            opt_lines.append(
                f"  - element_id={opt.element_id} | type={opt.control_type}{val_part} | text={opt.text!r}"
            )
        blocks.append(
            f"QUESTION #{q.question_number} (id={q.question_id}, type={q.question_type}):\n"
            f"Prompt: {q.prompt}\n"
            f"Options:\n" + "\n".join(opt_lines)
        )

    questions_payload = "\n\n".join(blocks)
    return (
        f"QUIZ URL: {url}\n"
        f"QUIZ TITLE: {title}\n"
        f"TOTAL EXTRACTED QUESTIONS: {len(questions)}\n\n"
        f"{questions_payload}\n\n"
        f"Return a BatchPlan covering all {len(questions)} questions."
    )


def build_batch_prompt(
    page_state: Dict[str, Any],
    user_data: Dict[str, Any] | None = None,
) -> str:
    elements_text = []
    for element in page_state.get("elements", []):
        elements_text.append(
            f"ELEMENT ID: {element.get('id')} | TAG: {element.get('tag')} | "
            f"TYPE: {element.get('type')} | NAME: {element.get('name')} | "
            f"TEXT: {element.get('text')} | VALUE: {element.get('value')} | "
            f"CONTEXT: {element.get('context')}"
        )

    return (
        f"CURRENT TEST PAGE\n"
        f"URL: {page_state.get('url')}\n"
        f"TITLE: {page_state.get('title')}\n"
        f"PAGE TEXT:\n{str(page_state.get('text') or '')[:6000]}\n\n"
        f"INTERACTIVE ELEMENTS:\n" + "\n".join(elements_text) + "\n\n"
        f"USER DATA: {user_data or {}}\n"
        f"Analyze the complete test and return a BatchPlan for all visible questions."
    )
