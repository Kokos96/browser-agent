from dataclasses import dataclass, field
from typing import Any


@dataclass
class InputField:
    type: str = "text"
    name: str = ""
    placeholder: str = ""
    value: str = ""


@dataclass
class PageState:
    url: str
    title: str
    headings: list[str] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)
    inputs: list[InputField] = field(default_factory=list)
    text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "headings": self.headings,
            "buttons": self.buttons,
            "inputs": [input_field.__dict__ for input_field in self.inputs],
            "text": self.text,
        }