import json
import os
import re
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Union

from ..browser import BrowserController
from ..config import settings
from ..llm import GeminiClient
from ..models import (
    Element,
    PageCategory,
    PageState,
    ProgressEvent,
    ProgressEventType,
    QuestionExtractionResult,
    WorkflowState,
)
from .action_executor import BrowserActionExecutor
from .answer_planner import AnswerPlanningAgent
from .navigation_agent import NavigationAndLaunchAgent
from .question_extractor import QuizQuestionExtractor
from .state_detector import QuizPageStateDetector
from .submission_agent import SubmissionAndResultAgent


class QuizOrchestrator:
    """Agent A: State-machine orchestrator coordinating specialist Agents B, C, D, E, F, and G."""

    def __init__(
        self,
        browser: BrowserController,
        llm: GeminiClient,
    ):
        self.browser = browser
        self.llm = llm

        # Specialist agents
        self.navigation_agent = NavigationAndLaunchAgent()
        self.question_extractor = QuizQuestionExtractor()
        self.state_detector = QuizPageStateDetector(
            navigation_agent=self.navigation_agent,
            question_extractor=self.question_extractor,
        )
        self.action_executor = BrowserActionExecutor()
        self.submission_agent = SubmissionAndResultAgent()

        self.device_id = os.getenv("DEVICE_ID", "local")
        self.user_data = {
            "surname": settings.user_surname,
            "name": settings.user_name,
            "group": settings.user_group,
        }

        self.state: WorkflowState = WorkflowState.INITIALIZING
        self.history: List[Dict[str, Any]] = []
        self.events: List[ProgressEvent] = []
        self.run_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.run_started_clock = time.monotonic()

        self.run_record: Dict[str, Any] = {
            "device_id": self.device_id,
            "run_id": os.getenv("RUN_ID") or self.run_started_at,
            "model": settings.gemini_model,
            "model_provider": "Google Gemini",
            "started_at": self.run_started_at,
            "finished_at": None,
            "duration_seconds": None,
            "status": "running",
            "workflow_state": self.state.value,
            "test_outcome": "pending",
            "score": None,
            "url": settings.test_url,
            "questions_detected": 0,
            "answers_planned": 0,
            "answers_selected": 0,
            "answers_failed": 0,
            "questions_unanswered": 0,
            "submission_attempted": False,
            "submission_confirmed": False,
            "actions": [],
            "events": [],
        }

    # --- Static / Compatibility Helpers ---

    @staticmethod
    def is_usable_question_plan(total_questions: int, selections: Any) -> bool:
        """A plan is usable only when it includes both questions and selections."""
        return int(total_questions or 0) > 0 and bool(selections)

    @classmethod
    def extract_score(cls, result_text: str) -> Optional[str]:
        return SubmissionAndResultAgent.extract_score(result_text)

    @staticmethod
    def filter_unanswered_questions(
        extraction: QuestionExtractionResult,
        answered_signatures: Set[str],
    ) -> QuestionExtractionResult:
        """Remove all questions already answered on this page, including the all-answered case."""
        unanswered = [
            question for question in extraction.questions
            if f"{question.container_signature}:{question.prompt}" not in answered_signatures
        ]
        if len(unanswered) == len(extraction.questions):
            return extraction
        return QuestionExtractionResult(
            questions=unanswered,
            submit_control_id=extraction.submit_control_id,
            next_control_id=extraction.next_control_id,
            page_readiness_notes=extraction.page_readiness_notes,
        )

    @staticmethod
    def _clean_log_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @classmethod
    def find_start_button(
        cls,
        elements: Sequence[Union[Element, Dict[str, Any]]],
    ) -> Optional[Union[Element, Dict[str, Any]]]:
        return NavigationAndLaunchAgent.find_start_button(elements)

    @classmethod
    def rank_start_controls(
        cls,
        elements: Sequence[Union[Element, Dict[str, Any]]],
    ):
        return NavigationAndLaunchAgent.rank_start_controls(elements)

    @staticmethod
    def find_by_name(
        elements: Sequence[Union[Element, Dict[str, Any]]],
        name: str,
    ) -> Optional[Union[Element, Dict[str, Any]]]:
        return NavigationAndLaunchAgent.find_by_name(elements, name)

    @staticmethod
    def find_submit_button(
        state: Union[PageState, Dict[str, Any], Sequence[Union[Element, Dict[str, Any]]]]
    ) -> Optional[int]:
        return SubmissionAndResultAgent.find_submit_button(state)

    def is_loading(self, state: Union[PageState, Dict[str, Any]]) -> bool:
        return self.state_detector.is_loading(state)

    def is_empty_test_page(self, state: Union[PageState, Dict[str, Any]]) -> bool:
        ps = PageState.from_any(state)
        url = (ps.url or "").lower()
        text = (ps.text or "").strip()
        if url.rstrip("/").endswith("/start") and not ps.elements:
            return True
        if not ps.elements and len(text) < 150:
            return True
        return False

    async def bootstrap(self, state: Dict[str, Any]) -> bool:
        elements = state.get("elements", [])
        for field_key, user_key in (("surname", "surname"), ("name", "name"), ("grp", "group")):
            element = self.find_by_name(elements, field_key)
            if element:
                val = (element.get("value") or "").strip()
                if not val and self.user_data.get(user_key):
                    print(f"Browser: filling {user_key}")
                    await self.browser.fill(element["id"], self.user_data[user_key])
                    await self.browser.wait(200)
                    return True

        start_element = self.find_start_button(elements)
        if start_element is not None:
            el_id = start_element.id if isinstance(start_element, Element) else start_element["id"]
            label = (
                (start_element.text or start_element.aria_label or "")
                if isinstance(start_element, Element)
                else (start_element.get("text") or start_element.get("aria_label") or "")
            )
            print("Browser: starting test", f"(element {el_id}: {label})")
            await self.browser.click(el_id)
            await self.browser.wait(1000)
            return True

        return False

    # --- State & Event Tracking ---

    def _transition(
        self,
        new_state: WorkflowState,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.state = new_state
        self.run_record["workflow_state"] = new_state.value
        if message:
            self.emit_event(
                ProgressEventType.STATE_TRANSITION,
                message=message,
                metadata={"workflow_state": new_state.value, **(metadata or {})},
            )

    def emit_event(
        self,
        event_type: ProgressEventType,
        message: str,
        question_index: Optional[int] = None,
        total_questions: Optional[int] = None,
        question_text: Optional[str] = None,
        selected_option: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProgressEvent:
        ev = ProgressEvent(
            event_type=event_type,
            workflow_state=self.state,
            message=message,
            question_index=question_index,
            total_questions=total_questions,
            question_text=question_text,
            selected_option=selected_option,
            status=status,
            metadata=metadata or {},
        )
        self.events.append(ev)
        print(message)
        return ev

    # --- Top-Level Execution ---

    async def run(self) -> bool:
        try:
            return await self._run_internal()
        except Exception as error:
            print()
            print("AGENT FATAL ERROR:", error)
            self.state = WorkflowState.FAILED
            self.run_record["workflow_state"] = self.state.value
            self.run_record["status"] = "error"
            self.run_record["error"] = str(error)
            if self.run_record.get("test_outcome") in (None, "unknown", "pending"):
                self.run_record["test_outcome"] = "unhandled_exception"
            await self.save_history()
            return False

    async def _run_internal(self) -> bool:
        print("Agent started")
        print("URL:", settings.test_url)

        self._transition(
            WorkflowState.NAVIGATING,
            f"Перехід на сторінку тесту: {settings.test_url}",
        )
        await self.browser.open(settings.test_url)

        # ---------------------------------------------------------
        # PHASE 1: Page State Detection + Start/Bootstrap Transition
        # ---------------------------------------------------------
        max_bootstrap_steps = max(3, getattr(settings, "max_launch_attempts", 3) + 4)
        clicked_start_ids: Set[int] = set()
        state: Dict[str, Any] = {}

        for step in range(1, max_bootstrap_steps + 1):
            print()
            print("=" * 60)
            print(f"BOOTSTRAP STEP {step}")
            print("=" * 60)

            state = await self.browser.inspect()
            print("Page:", state.get("title"))
            print("URL:", state.get("url"))
            print("Interactive elements:", len(state.get("elements") or []))

            self._transition(WorkflowState.DETECTING_PAGE_STATE)
            extraction = self.question_extractor.extract(state)
            classification = self.state_detector.classify(state, extraction=extraction)

            self.emit_event(
                ProgressEventType.PAGE_CLASSIFIED,
                message=(
                    f"Класифікація сторінки: {classification.category.value} "
                    f"(питань: {classification.detected_questions_count}, "
                    f"впевненість: {classification.confidence:.2f})"
                ),
                metadata=classification.model_dump(),
            )

            if classification.category == PageCategory.LOADING_STATE:
                print("Browser: waiting for Render...")
                await self.browser.wait(1500)
                continue

            if classification.category == PageCategory.CAPTCHA_REQUIRED:
                error_msg = "Виявлено CAPTCHA або захист від автоматизації. Автоматичне проходження зупинено."
                self._transition(WorkflowState.CAPTCHA_REQUIRED)
                self.run_record.update({
                    "status": "error",
                    "test_outcome": "captcha_required",
                    "failure_stage": "access_verification",
                    "error": error_msg,
                })
                self.emit_event(ProgressEventType.RUN_FAILED, message="ERROR: " + error_msg, status="error")
                await self.save_history()
                return False

            if classification.category in (PageCategory.AUTH_REQUIRED, PageCategory.ACCESS_GATE):
                error_msg = "Сторінка тесту вимагає авторизації або входу в обліковий запис."
                self._transition(WorkflowState.ACCESS_REQUIRED)
                self.run_record.update({
                    "status": "error",
                    "test_outcome": "access_required",
                    "failure_stage": "access_verification",
                    "error": error_msg,
                })
                self.emit_event(ProgressEventType.RUN_FAILED, message="ERROR: " + error_msg, status="error")
                await self.save_history()
                return False

            # If active quiz questions are already present on the page, check if profile fields still need filling
            filled = await self.navigation_agent.fill_optional_profile_fields(
                self.browser, state, self.user_data
            )
            if filled:
                continue

            if classification.category == PageCategory.ACTIVE_QUIZ:
                break

            # If on a landing page with a start control, click it and wait for quiz transition
            if classification.category == PageCategory.LANDING_PAGE and classification.start_candidates:
                candidate = next(
                    (c for c in classification.start_candidates if c.element_id not in clicked_start_ids),
                    None,
                )
                if candidate is not None:
                    clicked_start_ids.add(candidate.element_id)
                    self._transition(
                        WorkflowState.STARTING_TEST,
                        f"Знайдено кнопку запуску [{candidate.element_id}] «{candidate.text}» — перехід до тесту...",
                    )
                    prev_fp = str(state.get("dom_fingerprint") or "")
                    await self.browser.click(candidate.element_id)
                    self._transition(WorkflowState.WAITING_FOR_QUIZ)
                    if hasattr(self.browser, "wait_for_transition"):
                        await self.browser.wait_for_transition(
                            previous_fingerprint=prev_fp,
                            timeout_ms=getattr(settings, "transition_wait_ms", 1200),
                        )
                    else:
                        await self.browser.wait(1000)
                    continue

            break

        # ---------------------------------------------------------
        # PHASE 2: Wait for Test Content if Empty
        # ---------------------------------------------------------
        print()
        print("=" * 60)
        print("WAITING FOR TEST")
        print("=" * 60)

        state = await self.browser.inspect()
        if self.is_empty_test_page(state):
            print("Browser: /start is still loading.")
            self._transition(WorkflowState.WAITING_FOR_QUIZ)
            state = await self.browser.wait_for_test(settings.test_load_timeout)

        print("Test URL:", state.get("url"))
        print("Interactive elements:", len(state.get("elements") or []))
        print("Page text length:", len(state.get("text") or ""))

        if self.is_empty_test_page(state):
            self.save_debug_state(state)
            self._transition(WorkflowState.QUESTION_DETECTION_FAILED)
            self.run_record.update({
                "status": "error",
                "test_outcome": "test_content_not_loaded",
                "error": "Test content did not load before the configured timeout.",
                "failure_stage": "test_page_load",
                "diagnostics": {
                    "url": state.get("url") or "",
                    "title": state.get("title") or "",
                    "interactive_elements": len(state.get("elements") or []),
                    "page_text_excerpt": str(state.get("text") or "")[:2000],
                },
            })
            print("ERROR: test content did not load.")
            await self.save_history()
            return False

        # ---------------------------------------------------------
        # PHASE 3: Multi-Page Question Extraction, Planning & Execution
        # ---------------------------------------------------------
        planner = AnswerPlanningAgent(
            llm=self.llm,
            max_llm_calls=getattr(settings, "max_llm_calls", 5),
        )
        max_transitions = getattr(settings, "max_page_transitions", 25)
        page_step = 0
        total_detected = 0
        total_planned = 0
        total_selected = 0
        total_failed = 0
        answered_signatures: Set[str] = set()

        while page_step <= max_transitions:
            page_step += 1
            if settings.enable_vision:
                state["screenshot_path"] = await self.browser.screenshot(999)
            else:
                state["screenshot_path"] = None

            self._transition(WorkflowState.EXTRACTING_QUESTIONS)
            extraction: QuestionExtractionResult = self.question_extractor.extract(state)

            # Filter out questions already answered on the same page. This must
            # also handle the case where every extracted question was already answered.
            extraction = self.filter_unanswered_questions(
                extraction, answered_signatures
            )

            radio_count = sum(1 for el in state.get("elements", []) if (el.get("type") or "").lower() == "radio")
            checkbox_count = sum(1 for el in state.get("elements", []) if (el.get("type") or "").lower() == "checkbox")
            print()
            print("Radio controls:", radio_count)
            print("Checkbox controls:", checkbox_count)
            print("Extracted structured questions:", len(extraction.questions))

            # CRITICAL RULE: Never call Gemini if 0 answerable questions were extracted!
            if not extraction.questions:
                if total_detected == 0:
                    self.save_debug_state(state)
                    error_message = (
                        "На сторінці не знайдено жодного питання або варіанту відповіді тесту. "
                        "Виклик ШІ скасовано, тест не відправлявся."
                    )
                    self._transition(WorkflowState.QUESTION_DETECTION_FAILED)
                    self.run_record.update({
                        "status": "error",
                        "test_outcome": "questions_not_detected",
                        "failure_stage": "question_detection",
                        "error": error_message,
                        "questions_detected": 0,
                        "answers_planned": 0,
                        "answers_selected": 0,
                    })
                    self.emit_event(ProgressEventType.RUN_FAILED, message="ERROR: " + error_message, status="error")
                    await self.save_history()
                    return False
                # Already answered questions on prior pages; proceed to submission
                break

            self.emit_event(
                ProgressEventType.QUESTIONS_EXTRACTED,
                message=f"Вилучено питань на сторінці: {len(extraction.questions)}",
                total_questions=total_detected + len(extraction.questions),
            )

            self._transition(WorkflowState.PLANNING_ANSWERS)
            try:
                validated_plan = await planner.plan_answers(
                    state=state,
                    extraction=extraction,
                    user_data=self.user_data,
                )
                self.run_record["gemini_usage"] = (
                    getattr(self.llm, "total_usage", None)
                    or getattr(self.llm, "last_usage", {})
                )
            except Exception as error:
                print()
                print("AI STOP:", error)
                self._transition(WorkflowState.FAILED)
                self.run_record.update({
                    "status": "error",
                    "error": str(error),
                    "test_outcome": "answer_planning_failed",
                    "failure_stage": "llm_planning",
                })
                self.history.append({"error": str(error), "state": state})
                await self.save_history()
                return False

            raw_plan = validated_plan.raw_plan
            raw_total = int(raw_plan.total_questions or 0) if raw_plan else 0
            raw_selections = raw_plan.selections if raw_plan else []

            if not self.is_usable_question_plan(raw_total, raw_selections):
                error_message = (
                    "ШІ не розпізнав жодного питання тесту. "
                    "Відповіді не вибиралися, тест не відправлявся."
                )
                self._transition(WorkflowState.QUESTION_DETECTION_FAILED)
                print("ERROR: " + error_message)
                self.run_record.update({
                    "status": "error",
                    "test_outcome": "questions_not_detected",
                    "failure_stage": "question_detection",
                    "error": error_message,
                    "questions_detected": 0,
                    "answers_planned": 0,
                    "answers_selected": 0,
                })
                await self.save_history()
                return False

            if not validated_plan.actions:
                error_message = (
                    "ШІ сформував план, але жоден елемент відповіді не збігається "
                    "з поточною сторінкою."
                )
                self._transition(WorkflowState.ANSWERS_NOT_SELECTABLE)
                print("ERROR: " + error_message)
                self.run_record.update({
                    "status": "error",
                    "test_outcome": "answer_elements_not_found",
                    "failure_stage": "answer_selection",
                    "error": error_message,
                    "questions_detected": len(extraction.questions),
                    "answers_planned": len(raw_selections),
                    "answers_selected": 0,
                })
                await self.save_history()
                return False

            # Offset question numbers across multi-page quizzes
            for idx, act in enumerate(validated_plan.actions, start=1):
                act.question_number = total_detected + idx

            page_q_count = max(len(extraction.questions), len(validated_plan.actions))
            total_detected += page_q_count
            total_planned += len(validated_plan.actions)
            validated_plan.total_questions = total_detected

            self.run_record["questions_detected"] = total_detected
            self.run_record["answers_planned"] = total_planned

            self._transition(WorkflowState.SELECTING_ANSWERS)

            def _record_progress_event(ev: ProgressEvent) -> None:
                self.events.append(ev)
                print(ev.message)

            exec_result = await self.action_executor.execute_plan(
                browser=self.browser,
                plan=validated_plan,
                current_state=state,
                on_event=_record_progress_event,
            )

            total_selected += exec_result.answers_selected
            total_failed += exec_result.answers_failed
            self.run_record["answers_selected"] = total_selected
            self.run_record["answers_failed"] = total_failed
            self.run_record["questions_unanswered"] = max(0, total_detected - total_selected)

            for r in exec_result.results:
                if r.status == "selected":
                    self.history.append({
                        "question": r.question_number,
                        "element_id": r.element_id,
                        "answer": r.selected_option,
                        "context": r.question_prompt,
                        "verified": r.verified,
                    })

            for q in extraction.questions:
                answered_signatures.add(f"{q.container_signature}:{q.prompt}")

            print(f"Вибрано відповідей: {total_selected}/{total_detected}")

            if total_selected == 0:
                error_message = "Не вдалося вибрати жодної відповіді на сторінці тесту."
                self._transition(WorkflowState.ANSWERS_NOT_SELECTABLE)
                self.run_record.update({
                    "status": "error",
                    "test_outcome": "answer_selection_failed",
                    "failure_stage": "answer_selection",
                    "error": error_message,
                })
                await self.save_history()
                return False

            # Inspect page after selecting answers to check for multi-step Next button vs Submit button
            state = await self.browser.inspect()
            post_ext = self.question_extractor.extract(state)
            submit_id = post_ext.submit_control_id or self.find_submit_button(state)
            next_id = post_ext.next_control_id or extraction.next_control_id

            if next_id is not None and submit_id is None:
                self._transition(
                    WorkflowState.ADVANCING_PAGE,
                    f"Перехід до наступного питання тесту (елемент {next_id})...",
                )
                prev_fp = str(state.get("dom_fingerprint") or "")
                await self.browser.click(next_id)
                if hasattr(self.browser, "wait_for_transition"):
                    state = await self.browser.wait_for_transition(
                        previous_fingerprint=prev_fp,
                        timeout_ms=getattr(settings, "transition_wait_ms", 1200),
                    )
                else:
                    await self.browser.wait(1000)
                    state = await self.browser.inspect()

                post_class = self.state_detector.classify(state)
                if post_class.category == PageCategory.RESULT_PAGE:
                    break
                continue

            break

        print()
        print("=" * 60)
        print("ALL PLANNED ANSWERS EXECUTED")
        print("=" * 60)

        # ---------------------------------------------------------
        # PHASE 4: Submission & Result Verification (Agent G)
        # ---------------------------------------------------------
        self._transition(WorkflowState.SUBMITTING)
        final_state = await self.browser.inspect()
        sub_result = await self.submission_agent.submit_and_verify(
            browser=self.browser,
            state=final_state,
            auto_submit=settings.auto_submit,
            wait_ms=1500,
        )

        self.run_record["submission_attempted"] = sub_result.submission_attempted
        self.run_record["submission_confirmed"] = sub_result.submission_confirmed

        if (
            not sub_result.submission_confirmed
            and not sub_result.awaiting_manual_submission
        ):
            if sub_result.submit_control_not_found:
                error_message = (
                    "Відповіді вибрано, але кнопку завершення або результат тесту "
                    "не знайдено. Завершення тесту не підтверджено."
                )
            else:
                error_message = (
                    "Кнопку надсилання натиснуто, але видимий перехід до результату "
                    "не підтверджено. Перевірте тест вручну."
                )
            self._transition(WorkflowState.SUBMISSION_UNCONFIRMED)
            print("ERROR: " + error_message)
            self.run_record.update({
                "status": "error",
                "test_outcome": sub_result.test_outcome or "submission_unconfirmed",
                "failure_stage": "submission",
                "error": error_message,
            })
            await self.save_history()
            return False

        self._transition(WorkflowState.VERIFYING_RESULT)
        self.run_record["test_outcome"] = sub_result.test_outcome
        if sub_result.score_text:
            self.run_record["score"] = sub_result.score_text
        if sub_result.result_page_captured:
            self.run_record["result_page"] = {
                "url": sub_result.result_url,
                "title": sub_result.result_title,
                "text_excerpt": sub_result.result_excerpt,
            }

        self._transition(
            WorkflowState.COMPLETED,
            (
                f"Виконання завершено: вибрано {total_selected}/{total_detected} відповідей"
                + (f", результат: {sub_result.score_text}" if sub_result.score_text else ".")
            ),
        )
        self.run_record["status"] = "completed"
        await self.save_history()
        return True

    def save_debug_state(self, state: Dict[str, Any]) -> None:
        os.makedirs("results", exist_ok=True)
        with open("results/debug_state.json", "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2, default=str)
        print("Debug state saved: results/debug_state.json")

    async def save_history(self) -> None:
        """Save the run to local JSON and, when configured, Firestore."""
        os.makedirs("results", exist_ok=True)
        path = "results/agent_history.json"

        self.run_record["workflow_state"] = self.state.value
        self.run_record["actions"] = self.history
        self.run_record["events"] = [e.model_dump() for e in self.events]
        self.run_record["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self.run_record["duration_seconds"] = round(
            time.monotonic() - self.run_started_clock, 1
        )

        try:
            with open(path, "r", encoding="utf-8") as file:
                records = json.load(file)
            if not isinstance(records, list):
                records = []
        except (FileNotFoundError, json.JSONDecodeError):
            records = []

        run_id = self.run_record.setdefault("run_id", self.run_started_at)
        record = self.run_record.copy()
        index = next(
            (
                i
                for i, item in enumerate(records)
                if isinstance(item, dict)
                and item.get("run_id") == run_id
                and item.get("device_id", "local") == self.device_id
            ),
            None,
        )
        if index is None:
            records.append(record)
        else:
            records[index] = record

        fd, temp_path = tempfile.mkstemp(dir="results", prefix="history_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(records, file, ensure_ascii=False, indent=2, default=str)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

        print("History saved:", path)

        project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
        if not project_id:
            print("Firestore skipped: GOOGLE_CLOUD_PROJECT is not configured.")
            return

        try:
            from google.cloud import firestore

            db = firestore.Client(project=project_id)
            (
                db.collection("devices")
                .document(self.device_id)
                .collection("runs")
                .document(str(run_id))
                .set(record)
            )
            print("History saved: Firestore")
        except Exception as exc:
            print(f"Firestore save failed; local history is preserved: {exc}")
