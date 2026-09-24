from typing import Literal

from pydantic import BaseModel, Field


ActionType = Literal[
    "fill",
    "click",
    "select",
    "wait",
    "finish",
    "stop",
]


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


class PageState(BaseModel):
    url: str
    title: str
    text: str
    elements: list[Element]
    screenshot_path: str | None = None


class AgentAction(BaseModel):
    action: ActionType
    element_id: int | None = Field(
        default=None,
        description="ID of the element to interact with."
    )
    value: str | None = Field(
        default=None,
        description="Value to enter or select."
    )
    reason: str = Field(
        description="Short reason for the action."
    )