SYSTEM_PROMPT = """
You are the reasoning engine of an authorized browser automation
agent operating on a test environment.

Your task is to analyze ALL multiple-choice questions visible
on the current page and produce one answer selection for each
question.

IMPORTANT:

The browser will execute your selections automatically.

For every question:

1. Identify the question text.
2. Identify its available answer options.
3. Determine the best answer.
4. Select the element_id corresponding to that answer.

Rules:

1. Use ONLY element IDs present in the browser state.

2. Never invent element IDs.

3. Never invent answer options.

4. Use the context field to understand which question
   an answer control belongs to.

5. Return one selection for each question.

6. If a question has radio buttons, select exactly one.

7. If a question genuinely allows multiple selections,
   select all required options.

8. Do not click navigation buttons.

9. Do not click the final submit button.

10. Do not perform browser actions yourself.

11. Return the complete plan in one response.

12. Keep reasons very short.

13. The goal is to minimize the number of API requests.

14. Do not stop after the first question.

15. Analyze the entire page before producing the plan.
"""


def build_batch_prompt(
    page_state: dict,
    user_data: dict
) -> str:

    elements_text = []

    for element in page_state["elements"]:

        element_id = element.get(
            "id"
        )

        tag = element.get(
            "tag"
        )

        element_type = element.get(
            "type"
        )

        name = element.get(
            "name"
        )

        text = element.get(
            "text"
        )

        value = element.get(
            "value"
        )

        context = element.get(
            "context"
        )

        elements_text.append(
            f"""
ELEMENT ID: {element_id}
TAG: {tag}
TYPE: {element_type}
NAME: {name}
OPTION TEXT: {text}
VALUE: {value}
QUESTION CONTEXT: {context}
"""
        )

    return f"""
CURRENT TEST PAGE

URL:
{page_state["url"]}

TITLE:
{page_state["title"]}

PAGE TEXT:
{page_state["text"]}

INTERACTIVE ELEMENTS:

{"".join(elements_text)}

USER DATA:
{user_data}

Analyze the complete test.

Return a complete answer plan for ALL visible
multiple-choice questions.

Do not return a partial plan.
"""