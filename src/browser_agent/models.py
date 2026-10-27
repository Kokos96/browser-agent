from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class WorkflowState(str, Enum):
    INITIALIZING = "INITIALIZING"
    NAVIGATING = "NAVIGATING"
    DETECTING_PAGE_STATE = "DETECTING_PAGE_STATE"
    STARTING_TEST = "STARTING_TEST"
    WAITING_FOR_QUIZ = "WAITING_FOR_QUIZ"
    EXTRACTING_QUESTIONS = "EXTRACTING_QUESTIONS"
    PLANNING_ANSWERS = "PLANNING_ANSWERS"
    SELECTING_ANSWERS = "SELECTING_ANSWERS"
    ADVANCING_PAGE = "ADVANCING_PAGE"
    SUBMITTING = "SUBMITTING"
    VERIFYING_RESULT = "VERIFYING_RESULT"
    COMPLETED = "COMPLETED"
    # Terminal failure states
    QUESTION_DETECTION_FAILED = "QUESTION_DETECTION_FAILED"
    ANSWERS_NOT_SELECTABLE = "ANSWERS_NOT_SELECTABLE"
    ACCESS_REQUIRED = "ACCESS_REQUIRED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    SUBMISSION_UNCONFIRMED = "SUBMISSION_UNCONFIRMED"
    RESULT_UNAVAILABLE = "RESULT_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class PageCategory(str, Enum):
    LANDING_PAGE = "LANDING_PAGE"
    ACTIVE_QUIZ = "ACTIVE_QUIZ"
    LOADING_STATE = "LOADING_STATE"
    RESULT_PAGE = "RESULT_PAGE"
    ACCESS_GATE = "ACCESS_GATE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    UNKNOWN_PAGE = "UNKNOWN_PAGE"


class ProgressEventType(str, Enum):
    STATE_TRANSITION = "state_transition"
    PAGE_CLASSIFIED = "page_classified"
    START_CONTROL_FOUND = "start_control_found"
    START_CONTROL_CLICKED = "start_control_clicked"
    QUESTIONS_EXTRACTED = "questions_extracted"
    ANSWERS_PLANNED = "answers_planned"
    QUESTION_STARTED = "question_started"
    ANSWER_SELECTED = "answer_selected"
    ANSWER_SELECTION_FAILED = "answer_selection_failed"
    PAGE_ADVANCED = "page_advanced"
    SUBMISSION_ATTEMPTED = "submission_attempted"
    SUBMISSION_CONFIRMED = "submission_confirmed"
    RESULT_CAPTURED = "result_captured"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class Element(BaseModel):
    id: int
    tag: str
    role: Optional[str] = None
    type: Optional[str] = None
    name: Optional[str] = None
    text: str = ""
    placeholder: Optional[str] = None
    value: Optional[str] = None
    checked: Optional[bool] = None
    selected: Optional[bool] = None
    disabled: bool = False
    visible: bool = True
    in_nav_or_footer: bool = False
    aria_label: Optional[str] = None
    title: Optional[str] = None
    html_id: Optional[str] = None
    href: Optional[str] = None
    context: str = ""
    question_heading: str = ""
    container_id: str = ""
    select_options: List[Dict[str, str]] = Field(default_factory=list)

    @classmethod
    def from_any(cls, item: Union["Element", Dict[str, Any]]) -> "Element":
        if isinstance(item, cls):
            return item
        return cls(**{k: v for k, v in item.items() if k in cls.model_fields})


class PageState(BaseModel):
    url: str = ""
    title: str = ""
    text: str = ""
    elements: List[Element] = Field(default_factory=list)
    screenshot_path: Optional[str] = None
    dom_fingerprint: str = ""

    @property
    def page_text(self) -> str:
        return self.text

    @classmethod
    def from_any(cls, state: Union["PageState", Dict[str, Any]]) -> "PageState":
        if isinstance(state, cls):
            return state
        raw_elements = state.get("elements") or []
        elements = [Element.from_any(el) for el in raw_elements]
        return cls(
            url=str(state.get("url") or ""),
            title=str(state.get("title") or ""),
            text=str(state.get("text") or state.get("page_text") or ""),
            elements=elements,
            screenshot_path=state.get("screenshot_path"),
            dom_fingerprint=str(state.get("dom_fingerprint") or ""),
        )


class StartControlCandidate(BaseModel):
    element_id: int
    score: float
    text: str
    tag: str
    role: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)


class PageClassification(BaseModel):
    category: PageCategory
    confidence: float
    evidence: List[str] = Field(default_factory=list)
    recommended_action: str
    detected_questions_count: int = 0
    start_candidates: List[StartControlCandidate] = Field(default_factory=list)


class ExtractedOption(BaseModel):
    option_id: str
    element_id: int
    text: str
    value: Optional[str] = None
    control_type: str = "radio"
    checked: bool = False
    locator_hint: Dict[str, Any] = Field(default_factory=dict)


class ExtractedQuestion(BaseModel):
    question_id: str
    question_number: int
    prompt: str
    question_type: str = "single_choice"
    container_signature: str = ""
    options: List[ExtractedOption] = Field(default_factory=list)
    required: bool = True
    answered: bool = False


class QuestionExtractionResult(BaseModel):
    questions: List[ExtractedQuestion] = Field(default_factory=list)
    submit_control_id: Optional[int] = None
    next_control_id: Optional[int] = None
    page_readiness_notes: List[str] = Field(default_factory=list)


class BatchSelection(BaseModel):
    question_number: int = Field(description="Question number starting from 1.")
    element_id: int = Field(description="ID of the answer element to select.")
    option_value: Optional[str] = Field(
        default=None,
        description="Option value when selecting from a <select> dropdown.",
    )
    question_text: str = Field(
        default="",
        description="Short excerpt of the question prompt.",
    )
    option_text: str = Field(
        default="",
        description="Visible text of the chosen option.",
    )
    confidence: float = Field(
        default=0.9,
        description="Confidence score between 0.0 and 1.0.",
    )
    reason: str = Field(
        default="",
        description="Very short reason for the selected answer.",
    )


class BatchPlan(BaseModel):
    selections: List[BatchSelection] = Field(
        description="One selected answer for each question visible on the page."
    )
    total_questions: int = Field(
        description="Number of questions identified on the page."
    )
    reason: str = Field(
        default="",
        description="Short summary of the answer plan.",
    )


class ValidatedAnswerAction(BaseModel):
    question_id: str
    question_number: int
    question_prompt: str
    element_id: int
    option_text: str
    option_value: Optional[str] = None
    control_type: str = "radio"
    locator_hint: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.9
    reason: str = ""


class ValidatedAnswerPlan(BaseModel):
    actions: List[ValidatedAnswerAction] = Field(default_factory=list)
    total_questions: int = 0
    unplanned_question_ids: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    raw_plan: Optional[BatchPlan] = None


class QuestionActionResult(BaseModel):
    question_id: str
    question_number: int
    question_prompt: str
    element_id: int
    selected_option: str
    status: str = "selected"
    verified: bool = False
    error: Optional[str] = None


class BatchExecutionResult(BaseModel):
    results: List[QuestionActionResult] = Field(default_factory=list)
    answers_selected: int = 0
    answers_failed: int = 0
    questions_unanswered: int = 0


class SubmissionResult(BaseModel):
    submission_attempted: bool = False
    submission_confirmed: bool = False
    submit_control_id: Optional[int] = None
    submit_control_not_found: bool = False
    awaiting_manual_submission: bool = False
    result_page_captured: bool = False
    score_captured: bool = False
    score_text: Optional[str] = None
    test_outcome: str = "incomplete"
    result_excerpt: str = ""
    result_url: str = ""
    result_title: str = ""
    evidence: List[str] = Field(default_factory=list)


class ProgressEvent(BaseModel):
    event_type: ProgressEventType
    workflow_state: WorkflowState
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    message: str
    question_index: Optional[int] = None
    total_questions: Optional[int] = None
    question_text: Optional[str] = None
    selected_option: Optional[str] = None
    status: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
