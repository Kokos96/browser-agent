from pydantic import BaseModel, Field


class Element(BaseModel):
    id: int
    tag: str
    type: str | None = None
    name: str | None = None
    text: str = ""
    placeholder: str | None = None
    value: str | None = None
    checked: bool | None = None
    aria_label: str | None = None
    html_id: str | None = None
    context: str = ""


class PageState(BaseModel):
    url: str
    title: str
    text: str
    elements: list[Element]
    screenshot_path: str | None = None


class BatchSelection(BaseModel):
    question_number: int = Field(
        description="Question number starting from 1."
    )

    element_id: int = Field(
        description="ID of the answer element to select."
    )

    reason: str = Field(
        description="Very short reason for the selected answer."
    )


class BatchPlan(BaseModel):
    selections: list[BatchSelection] = Field(
        description=(
            "One selected answer for each multiple-choice "
            "question visible on the page."
        )
    )

    total_questions: int = Field(
        description="Number of questions the model identified."
    )

    reason: str = Field(
        description="Short summary of the plan."
    )