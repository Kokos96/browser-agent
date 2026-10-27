import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from browser_agent.agent import BrowserAgent, QuizOrchestrator
from browser_agent.agents import (
    AnswerPlanningAgent,
    BrowserActionExecutor,
    NavigationAndLaunchAgent,
    QuizPageStateDetector,
    QuizQuestionExtractor,
    SubmissionAndResultAgent,
)
from browser_agent.browser import BrowserController
from browser_agent.config import settings
from browser_agent.models import (
    BatchPlan,
    BatchSelection,
    Element,
    ExtractedQuestion,
    PageCategory,
    PageState,
    ProgressEventType,
    QuestionExtractionResult,
    ValidatedAnswerAction,
    ValidatedAnswerPlan,
    WorkflowState,
)


class StartPageDetectionTests(unittest.TestCase):
    """Category A: Start-page detection and ranking across UK/RU/EN."""

    def test_recognizes_common_take_the_quiz_button(self):
        ranked = NavigationAndLaunchAgent.rank_start_controls([
            {"id": 11, "tag": "button", "text": "Take the Quiz"},
            {"id": 12, "tag": "button", "text": "Subscribe to continue"},
        ])
        self.assertEqual([candidate.element_id for candidate in ranked], [11])

    def test_ranks_button_link_and_role_button_across_languages(self):
        elements = [
            {"id": 1, "tag": "a", "text": "Головна", "in_nav_or_footer": True},
            {"id": 2, "tag": "button", "text": "Почати тест"},
            {"id": 3, "tag": "a", "text": "Начать тест"},
            {"id": 4, "tag": "div", "role": "button", "aria_label": "Start quiz"},
            {"id": 5, "tag": "button", "text": "Take the test"},
        ]
        ranked = NavigationAndLaunchAgent.rank_start_controls(elements)
        self.assertEqual(len(ranked), 4)
        self.assertEqual(ranked[0].element_id, 2)
        self.assertGreaterEqual(ranked[0].score, 0.9)

    def test_rejects_restart_share_login_and_subscribe_buttons(self):
        elements = [
            {"id": 1, "tag": "button", "text": "Restart quiz"},
            {"id": 2, "tag": "button", "text": "Поділитися результатом"},
            {"id": 3, "tag": "button", "text": "Share on Telegram"},
            {"id": 4, "tag": "button", "text": "Sign in to start"},
            {"id": 5, "tag": "button", "text": "Subscribe"},
            {"id": 6, "tag": "button", "text": "Пройти тест заново"},
        ]
        self.assertEqual(NavigationAndLaunchAgent.rank_start_controls(elements), [])
        self.assertIsNone(BrowserAgent.find_start_button(elements))

    def test_penalizes_nav_or_footer_controls_below_main_cta(self):
        elements = [
            {"id": 10, "tag": "a", "text": "Start quiz", "in_nav_or_footer": True},
            {"id": 11, "tag": "button", "text": "Розпочати тест", "in_nav_or_footer": False},
        ]
        best = BrowserAgent.find_start_button(elements)
        self.assertIsNotNone(best)
        self.assertEqual(best["id"], 11)


class StateDetectorAndExtractorTests(unittest.TestCase):
    def test_filter_removes_all_questions_when_they_were_already_answered(self):
        extraction = QuestionExtractionResult(
            questions=[
                ExtractedQuestion(
                    question_id="q1",
                    question_number=1,
                    prompt="Question already answered?",
                    container_signature="container-1",
                )
            ],
            submit_control_id=9,
            next_control_id=8,
            page_readiness_notes=["page was inspected"],
        )

        filtered = QuizOrchestrator.filter_unanswered_questions(
            extraction,
            {"container-1:Question already answered?"},
        )

        self.assertEqual(filtered.questions, [])
        self.assertEqual(filtered.submit_control_id, 9)
        self.assertEqual(filtered.next_control_id, 8)
        self.assertEqual(filtered.page_readiness_notes, ["page was inspected"])

    def test_filter_preserves_unanswered_questions_on_same_page(self):
        extraction = QuestionExtractionResult(
            questions=[
                ExtractedQuestion(
                    question_id="q1",
                    question_number=1,
                    prompt="Already done?",
                    container_signature="c1",
                ),
                ExtractedQuestion(
                    question_id="q2",
                    question_number=2,
                    prompt="Still pending?",
                    container_signature="c2",
                ),
            ]
        )

        filtered = QuizOrchestrator.filter_unanswered_questions(
            extraction,
            {"c1:Already done?"},
        )

        self.assertEqual([q.question_id for q in filtered.questions], ["q2"])


    """Categories A, B, C: Page classification and structured question extraction."""

    def setUp(self):
        self.detector = QuizPageStateDetector()
        self.extractor = QuizQuestionExtractor()

    def test_giggleus_like_landing_page_with_46_links_is_classified_as_landing_not_quiz(self):
        # Simulate Giggleus landing page: 45 nav/footer/social links + 1 Start button, 0 questions
        elements = [
            {"id": i, "tag": "a", "text": f"Nav Link {i}", "in_nav_or_footer": True}
            for i in range(1, 46)
        ]
        elements.append({"id": 46, "tag": "button", "text": "Пройти тест", "in_nav_or_footer": False})

        state = {
            "url": "https://www.giggleus.com/uk/mental-age-quiz/",
            "title": "Тест на психологічний вік — Mental Age Quiz",
            "text": "Дізнайтеся свій психологічний вік за 10 питань! Натисніть кнопку нижче.",
            "elements": elements,
        }

        extraction = self.extractor.extract(state)
        self.assertEqual(len(extraction.questions), 0)

        classification = self.detector.classify(state, extraction=extraction)
        self.assertEqual(classification.category, PageCategory.LANDING_PAGE)
        self.assertEqual(classification.detected_questions_count, 0)
        self.assertEqual(classification.start_candidates[0].element_id, 46)

    def test_subscription_wall_is_classified_as_access_gate(self):
        state = {
            "url": "https://example.com/quiz",
            "title": "Premium Quiz",
            "text": "This quiz is available to subscribers. Subscription required to continue.",
            "elements": [
                {"id": 1, "tag": "button", "text": "Subscribe to continue"}
            ],
        }
        classification = self.detector.classify(state)
        self.assertEqual(classification.category, PageCategory.ACCESS_GATE)
        self.assertEqual(classification.recommended_action, "stop_access_required")

    def test_unknown_page_with_many_buttons_and_no_quiz_controls_is_unknown_page(self):
        elements = [
            {"id": i, "tag": "button", "text": f"Filter item {i}"}
            for i in range(1, 20)
        ]
        state = {
            "url": "https://example.com/catalog",
            "title": "Online Quiz Catalog",
            "text": "Browse our articles and catalog items below.",
            "elements": elements,
        }
        classification = self.detector.classify(state)
        self.assertEqual(classification.category, PageCategory.UNKNOWN_PAGE)
        self.assertEqual(classification.detected_questions_count, 0)

    def test_extracts_radio_checkbox_and_select_questions_accurately(self):
        state = {
            "url": "https://example.com/quiz",
            "title": "Python Test",
            "text": "1. Який тип даних є незмінним? 2. Оберіть фреймворк:",
            "elements": [
                {
                    "id": 1,
                    "tag": "input",
                    "type": "radio",
                    "name": "q1",
                    "text": "tuple",
                    "value": "tuple",
                    "question_heading": "1. Який тип даних є незмінним?",
                    "container_id": "q_cont_1",
                },
                {
                    "id": 2,
                    "tag": "input",
                    "type": "radio",
                    "name": "q1",
                    "text": "list",
                    "value": "list",
                    "question_heading": "1. Який тип даних є незмінним?",
                    "container_id": "q_cont_1",
                },
                {
                    "id": 3,
                    "tag": "select",
                    "name": "q2_framework",
                    "question_heading": "2. Оберіть веб-фреймворк Python:",
                    "select_options": [
                        {"value": "", "text": "Оберіть..."},
                        {"value": "fastapi", "text": "FastAPI"},
                        {"value": "express", "text": "Express.js"},
                    ],
                },
                {
                    "id": 4,
                    "tag": "button",
                    "text": "Завершити тест",
                },
            ],
        }
        ext = self.extractor.extract(state)
        self.assertEqual(len(ext.questions), 2)
        self.assertEqual(ext.questions[0].question_type, "single_choice")
        self.assertEqual(len(ext.questions[0].options), 2)
        self.assertEqual(ext.questions[1].question_type, "select_dropdown")
        self.assertEqual(len(ext.questions[1].options), 2)
        self.assertEqual(ext.submit_control_id, 4)


class ZeroQuestionFailureTests(unittest.TestCase):
    """Category C: Zero-question failure handling without calling Gemini or fabricating completion."""

    def test_zero_extracted_questions_never_calls_gemini_and_fails_cleanly(self):
        async def run_test():
            mock_browser = MagicMock()
            mock_browser.open = AsyncMock()
            mock_browser.wait = AsyncMock()
            mock_browser.wait_for_transition = AsyncMock()
            # Page has 30 generic navigation links and marketing text, no start button, no questions
            mock_browser.inspect = AsyncMock(
                return_value={
                    "url": "https://example.com/article",
                    "title": "Article about Quizzes",
                    "text": "This is a long article discussing how online quizzes work in education. " * 5,
                    "elements": [
                        {"id": i, "tag": "a", "text": f"Menu Link {i}", "in_nav_or_footer": True}
                        for i in range(1, 31)
                    ],
                    "dom_fingerprint": "fp_article",
                }
            )

            mock_llm = MagicMock()
            mock_llm.plan_test = AsyncMock()

            with tempfile.TemporaryDirectory() as tmp:
                orig_cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    agent = BrowserAgent(mock_browser, mock_llm)
                    success = await agent.run()
                    self.assertFalse(success)
                    # Crucial requirement: Gemini must NOT be called when 0 questions are extracted
                    mock_llm.plan_test.assert_not_called()
                    self.assertEqual(agent.state, WorkflowState.QUESTION_DETECTION_FAILED)
                    self.assertEqual(agent.run_record["status"], "error")
                    self.assertEqual(agent.run_record["test_outcome"], "questions_not_detected")
                    self.assertEqual(agent.run_record["failure_stage"], "question_detection")
                    self.assertEqual(agent.run_record["questions_detected"], 0)
                    self.assertEqual(agent.run_record["answers_selected"], 0)
                finally:
                    os.chdir(orig_cwd)

        asyncio.run(run_test())

    def test_gemini_returning_zero_questions_aborts_before_clicking_or_submitting(self):
        async def run_test():
            mock_browser = MagicMock()
            mock_browser.open = AsyncMock()
            mock_browser.wait = AsyncMock()
            mock_browser.select = AsyncMock()
            mock_browser.click = AsyncMock()
            mock_browser.inspect = AsyncMock(
                return_value={
                    "url": "https://example.com/quiz",
                    "title": "Quiz",
                    "text": "1. Question one?",
                    "elements": [
                        {"id": 1, "tag": "input", "type": "radio", "name": "q1", "text": "A"},
                        {"id": 2, "tag": "input", "type": "radio", "name": "q1", "text": "B"},
                        {"id": 3, "tag": "button", "text": "Submit"},
                    ],
                    "dom_fingerprint": "fp_q1",
                }
            )

            mock_llm = MagicMock()
            mock_llm.plan_test = AsyncMock(
                return_value=BatchPlan(selections=[], total_questions=0, reason="None")
            )

            with tempfile.TemporaryDirectory() as tmp:
                orig_cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    agent = BrowserAgent(mock_browser, mock_llm)
                    success = await agent.run()
                    self.assertFalse(success)
                    mock_browser.select.assert_not_called()
                    mock_browser.click.assert_not_called()
                    self.assertEqual(agent.run_record["status"], "error")
                    self.assertEqual(agent.run_record["test_outcome"], "questions_not_detected")
                    self.assertEqual(agent.run_record["answers_selected"], 0)
                finally:
                    os.chdir(orig_cwd)

        asyncio.run(run_test())


class DynamicDomAndObstructedClickTests(unittest.TestCase):
    """Categories D & E: Dynamic DOM re-resolution and obstructed click recovery."""

    def test_executor_reresolves_element_id_when_dom_indices_shift(self):
        action = ValidatedAnswerAction(
            question_id="q_1",
            question_number=1,
            question_prompt="Яка мова працює в браузері?",
            element_id=2,  # Original index was 2
            option_text="JavaScript",
            option_value="js",
            control_type="radio",
            locator_hint={
                "tag": "input",
                "type": "radio",
                "name": "lang",
                "value": "js",
                "text": "JavaScript",
                "html_id": "opt_js",
            },
        )
        # Banner inserted at index 1-2 shifted JavaScript option to id=9
        mutated_state = {
            "url": "https://example.com/quiz",
            "title": "Quiz",
            "text": "Яка мова працює в браузері?",
            "elements": [
                {"id": 1, "tag": "button", "text": "Close promo"},
                {"id": 2, "tag": "a", "text": "Promo banner"},
                {"id": 8, "tag": "input", "type": "radio", "name": "lang", "value": "py", "text": "Python", "html_id": "opt_py"},
                {"id": 9, "tag": "input", "type": "radio", "name": "lang", "value": "js", "text": "JavaScript", "html_id": "opt_js"},
            ],
        }
        resolved_id = BrowserActionExecutor.resolve_target_element_id(action, mutated_state)
        self.assertEqual(resolved_id, 9)

    def test_click_falls_back_to_visible_close_control_when_escape_does_not_clear_overlay(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as directory:
                page = MagicMock()
                target = MagicMock()
                target.scroll_into_view_if_needed = AsyncMock()
                # First two clicks fail due to overlay interception; third succeeds after close button click
                target.click = AsyncMock(
                    side_effect=[
                        PlaywrightError("element intercepts pointer events"),
                        PlaywrightError("element intercepts pointer events"),
                        None,
                    ]
                )

                close_btn = MagicMock()
                close_btn.is_visible = AsyncMock(return_value=True)
                close_btn.is_enabled = AsyncMock(return_value=True)
                close_btn.click = AsyncMock()

                close_locator = MagicMock()
                close_locator.count = AsyncMock(return_value=1)
                close_locator.nth.return_value = close_btn

                def locator_side_effect(selector):
                    if "data-browser-agent-element-id" in selector:
                        return target
                    return close_locator

                page.locator.side_effect = locator_side_effect
                page.keyboard.press = AsyncMock()
                page.wait_for_timeout = AsyncMock()

                controller = BrowserController(page, screenshots_dir=directory)
                await controller.click(3)

                self.assertEqual(target.click.await_count, 3)
                page.keyboard.press.assert_awaited_once_with("Escape")
                close_btn.click.assert_awaited_once()

        asyncio.run(run_test())


class ProgressEventsAndSubmissionTests(unittest.TestCase):
    """Categories F & G: Question-level progress events, multi-page advancing, and score verification."""

    def test_question_level_progress_events_and_score_capture(self):
        async def run_test():
            quiz_state = {
                "url": "https://example.com/quiz",
                "title": "JS Quiz",
                "text": "1. 2+2? 2. Capital of Ukraine?",
                "elements": [
                    {
                        "id": 1,
                        "tag": "input",
                        "type": "radio",
                        "name": "q1",
                        "text": "4",
                        "value": "4",
                        "question_heading": "1. Скільки буде 2+2?",
                        "container_id": "c1",
                    },
                    {
                        "id": 2,
                        "tag": "input",
                        "type": "radio",
                        "name": "q1",
                        "text": "5",
                        "value": "5",
                        "question_heading": "1. Скільки буде 2+2?",
                        "container_id": "c1",
                    },
                    {
                        "id": 3,
                        "tag": "input",
                        "type": "radio",
                        "name": "q2",
                        "text": "Київ",
                        "value": "kyiv",
                        "question_heading": "2. Столиця України?",
                        "container_id": "c2",
                    },
                    {
                        "id": 4,
                        "tag": "input",
                        "type": "radio",
                        "name": "q2",
                        "text": "Львів",
                        "value": "lviv",
                        "question_heading": "2. Столиця України?",
                        "container_id": "c2",
                    },
                    {
                        "id": 5,
                        "tag": "button",
                        "text": "Завершити тест",
                    },
                ],
                "dom_fingerprint": "fp_quiz",
            }
            result_state = {
                "url": "https://example.com/quiz/result",
                "title": "Результат",
                "text": "Вітаємо! Ваш результат: 2 / 2. Правильних відповідей: 2 з 2.",
                "elements": [],
                "dom_fingerprint": "fp_result",
            }

            mock_browser = MagicMock()
            mock_browser.open = AsyncMock()
            mock_browser.wait = AsyncMock()
            mock_browser.select = AsyncMock()
            mock_browser.verify_selection = AsyncMock(return_value=True)
            mock_browser.click = AsyncMock()
            mock_browser.inspect = AsyncMock(
                side_effect=[quiz_state, quiz_state, quiz_state, quiz_state, result_state]
            )

            mock_llm = MagicMock()
            mock_llm.plan_test = AsyncMock(
                return_value=BatchPlan(
                    selections=[
                        BatchSelection(question_number=1, element_id=1, reason="2+2=4"),
                        BatchSelection(question_number=2, element_id=3, reason="Kyiv is capital"),
                    ],
                    total_questions=2,
                    reason="Answered both questions",
                )
            )
            mock_llm.total_usage = {"input_tokens": 180, "output_tokens": 45, "thinking_tokens": 0, "total_tokens": 225}

            with tempfile.TemporaryDirectory() as tmp:
                orig_cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    agent = BrowserAgent(mock_browser, mock_llm)
                    success = await agent.run()
                    self.assertTrue(success)
                    self.assertEqual(agent.state, WorkflowState.COMPLETED)
                    self.assertEqual(agent.run_record["status"], "completed")
                    self.assertEqual(agent.run_record["test_outcome"], "score_captured")
                    self.assertEqual(agent.run_record["score"], "2/2")
                    self.assertEqual(agent.run_record["questions_detected"], 2)
                    self.assertEqual(agent.run_record["answers_planned"], 2)
                    self.assertEqual(agent.run_record["answers_selected"], 2)
                    self.assertEqual(agent.run_record["answers_failed"], 0)
                    self.assertEqual(agent.run_record["questions_unanswered"], 0)
                    self.assertTrue(agent.run_record["submission_attempted"])
                    self.assertTrue(agent.run_record["submission_confirmed"])

                    event_types = [e.event_type for e in agent.events]
                    self.assertIn(ProgressEventType.PAGE_CLASSIFIED, event_types)
                    self.assertIn(ProgressEventType.QUESTIONS_EXTRACTED, event_types)
                    self.assertEqual(event_types.count(ProgressEventType.QUESTION_STARTED), 2)
                    self.assertEqual(event_types.count(ProgressEventType.ANSWER_SELECTED), 2)
                finally:
                    os.chdir(orig_cwd)

        asyncio.run(run_test())

    def test_manual_submission_is_not_reported_as_confirmed(self):
        async def run_test():
            quiz_state = {
                "url": "https://example.com/quiz",
                "title": "Quiz",
                "text": "Question 1",
                "elements": [
                    {"id": 1, "tag": "button", "text": "Submit test"},
                ],
                "dom_fingerprint": "fp_quiz",
            }
            browser = MagicMock()
            browser.click = AsyncMock()
            browser.wait = AsyncMock()
            browser.inspect = AsyncMock(return_value=quiz_state)

            result = await SubmissionAndResultAgent().submit_and_verify(
                browser=browser,
                state=quiz_state,
                auto_submit=False,
            )

            self.assertTrue(result.awaiting_manual_submission)
            self.assertFalse(result.submission_attempted)
            self.assertFalse(result.submission_confirmed)
            self.assertEqual(result.test_outcome, "awaiting_manual_submission")
            browser.click.assert_not_awaited()

        asyncio.run(run_test())

    def test_click_without_observable_result_transition_is_unconfirmed(self):
        async def run_test():
            quiz_state = {
                "url": "https://example.com/quiz",
                "title": "Quiz",
                "text": "Question 1: Choose one answer",
                "elements": [
                    {
                        "id": 1, "tag": "input", "type": "radio", "name": "q1",
                        "text": "Option A", "value": "a",
                        "question_heading": "Question 1", "container_id": "q1",
                    },
                    {
                        "id": 2, "tag": "input", "type": "radio", "name": "q1",
                        "text": "Option B", "value": "b",
                        "question_heading": "Question 1", "container_id": "q1",
                    },
                    {"id": 3, "tag": "button", "text": "Submit test"},
                ],
                "dom_fingerprint": "same-quiz",
            }
            browser = MagicMock()
            browser.click = AsyncMock()
            browser.wait = AsyncMock()
            browser.inspect = AsyncMock(return_value=quiz_state)

            result = await SubmissionAndResultAgent().submit_and_verify(
                browser=browser,
                state=quiz_state,
                auto_submit=True,
                wait_ms=1,
            )

            self.assertTrue(result.submission_attempted)
            self.assertFalse(result.submission_confirmed)
            self.assertFalse(result.result_page_captured)
            self.assertEqual(result.test_outcome, "submission_unconfirmed")
            browser.click.assert_awaited_once_with(3)

        asyncio.run(run_test())

    def test_missing_submit_button_and_no_score_is_marked_as_error(self):
        async def run_test():
            quiz_state_no_submit = {
                "url": "https://example.com/quiz",
                "title": "Quiz",
                "text": "1. Question?",
                "elements": [
                    {"id": 1, "tag": "input", "type": "radio", "name": "q1", "text": "Option A", "container_id": "c1"},
                    {"id": 2, "tag": "input", "type": "radio", "name": "q1", "text": "Option B", "container_id": "c1"},
                ],
                "dom_fingerprint": "fp_nosub",
            }

            mock_browser = MagicMock()
            mock_browser.open = AsyncMock()
            mock_browser.wait = AsyncMock()
            mock_browser.select = AsyncMock()
            mock_browser.verify_selection = AsyncMock(return_value=True)
            mock_browser.click = AsyncMock()
            mock_browser.inspect = AsyncMock(return_value=quiz_state_no_submit)

            mock_llm = MagicMock()
            mock_llm.plan_test = AsyncMock(
                return_value=BatchPlan(
                    selections=[BatchSelection(question_number=1, element_id=1, reason="A")],
                    total_questions=1,
                    reason="One question",
                )
            )

            with tempfile.TemporaryDirectory() as tmp:
                orig_cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    agent = BrowserAgent(mock_browser, mock_llm)
                    success = await agent.run()
                    self.assertFalse(success)
                    self.assertEqual(agent.state, WorkflowState.SUBMISSION_UNCONFIRMED)
                    self.assertEqual(agent.run_record["status"], "error")
                    self.assertEqual(agent.run_record["test_outcome"], "submit_control_not_found")
                    self.assertEqual(agent.run_record["answers_selected"], 1)
                    self.assertFalse(agent.run_record["submission_confirmed"])
                finally:
                    os.chdir(orig_cwd)

        asyncio.run(run_test())

    def test_multipage_quiz_advances_via_next_button_and_submits_on_last_page(self):
        async def run_test():
            page1_state = {
                "url": "https://example.com/quiz?p=1",
                "title": "Step 1",
                "text": "Питання 1: Скільки бітів у байті?",
                "elements": [
                    {
                        "id": 1,
                        "tag": "input",
                        "type": "radio",
                        "name": "q1",
                        "text": "8",
                        "value": "8",
                        "question_heading": "Питання 1: Скільки бітів у байті?",
                        "container_id": "c1",
                    },
                    {
                        "id": 2,
                        "tag": "input",
                        "type": "radio",
                        "name": "q1",
                        "text": "10",
                        "value": "10",
                        "question_heading": "Питання 1: Скільки бітів у байті?",
                        "container_id": "c1",
                    },
                    {
                        "id": 3,
                        "tag": "button",
                        "text": "Наступне питання",
                    },
                ],
                "dom_fingerprint": "fp_p1",
            }
            page2_state = {
                "url": "https://example.com/quiz?p=2",
                "title": "Step 2",
                "text": "Питання 2: Який протокол шифрує веб-трафік?",
                "elements": [
                    {
                        "id": 10,
                        "tag": "input",
                        "type": "radio",
                        "name": "q2",
                        "text": "HTTPS",
                        "value": "https",
                        "question_heading": "Питання 2: Який протокол шифрує веб-трафік?",
                        "container_id": "c2",
                    },
                    {
                        "id": 11,
                        "tag": "input",
                        "type": "radio",
                        "name": "q2",
                        "text": "FTP",
                        "value": "ftp",
                        "question_heading": "Питання 2: Який протокол шифрує веб-трафік?",
                        "container_id": "c2",
                    },
                    {
                        "id": 12,
                        "tag": "button",
                        "text": "Завершити тест",
                    },
                ],
                "dom_fingerprint": "fp_p2",
            }
            result_state = {
                "url": "https://example.com/quiz/done",
                "title": "Done",
                "text": "You scored 2 out of 2!",
                "elements": [],
                "dom_fingerprint": "fp_done",
            }

            mock_browser = MagicMock()
            mock_browser.open = AsyncMock()
            mock_browser.wait = AsyncMock()
            mock_browser.select = AsyncMock()
            mock_browser.verify_selection = AsyncMock(return_value=True)
            mock_browser.click = AsyncMock()
            mock_browser.wait_for_transition = AsyncMock(return_value=page2_state)
            mock_browser.inspect = AsyncMock(
                side_effect=[
                    page1_state,  # bootstrap
                    page1_state,  # wait_for_test check
                    page1_state,  # post-select on page 1
                    page2_state,  # post-select on page 2
                    page2_state,  # final_state before submit
                    result_state,  # result inspection
                ]
            )

            mock_llm = MagicMock()
            mock_llm.plan_test = AsyncMock(
                side_effect=[
                    BatchPlan(
                        selections=[BatchSelection(question_number=1, element_id=1, reason="8 bits")],
                        total_questions=1,
                        reason="Page 1",
                    ),
                    BatchPlan(
                        selections=[BatchSelection(question_number=1, element_id=10, reason="HTTPS")],
                        total_questions=1,
                        reason="Page 2",
                    ),
                ]
            )

            with tempfile.TemporaryDirectory() as tmp:
                orig_cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    agent = BrowserAgent(mock_browser, mock_llm)
                    success = await agent.run()
                    self.assertTrue(success)
                    self.assertEqual(agent.run_record["questions_detected"], 2)
                    self.assertEqual(agent.run_record["answers_selected"], 2)
                    self.assertEqual(agent.run_record["score"], "2/2")
                    # Clicked Next (3) and Submit (12)
                    clicked_ids = [call.args[0] for call in mock_browser.click.await_args_list]
                    self.assertIn(3, clicked_ids)
                    self.assertIn(12, clicked_ids)
                finally:
                    os.chdir(orig_cwd)

        asyncio.run(run_test())


class RealPlaywrightLandingToQuizE2ETests(unittest.TestCase):
    """Category B: Real headless Playwright E2E test verifying Landing -> Start Click -> Quiz -> Submit -> Score."""

    def test_full_landing_to_quiz_to_result_transition_in_headless_chromium(self):
        html_fixture = """<!DOCTYPE html>
<html lang="uk">
<head>
  <meta charset="utf-8">
  <title>Тест з інформатики — Online Quiz Portal</title>
</head>
<body>
  <header>
    <nav>
      <a href="#home">Головна</a>
      <a href="#catalog">Каталог тестів</a>
      <a href="#about">Про проєкт</a>
      <button type="button" onclick="window.Shared=true">Поділитися</button>
    </nav>
  </header>

  <main id="app">
    <section id="landing-view">
      <h1>Перевірте свої знання з комп'ютерних мереж та Python</h1>
      <p>Цей тест містить 3 питання різних типів (радіокнопки, спадний список). Натисніть кнопку нижче, щоб почати.</p>
      <input type="text" name="surname" placeholder="Прізвище" />
      <input type="text" name="name" placeholder="Ім'я" />
      <button id="start-quiz-btn" type="button" onclick="startQuiz()">Розпочати тест</button>
    </section>
  </main>

  <footer>
    <a href="#privacy">Політика конфіденційності</a>
    <a href="#terms">Умови використання</a>
  </footer>

  <script>
    function startQuiz() {
      document.getElementById('app').innerHTML = `
        <form id="quiz-form" onsubmit="submitQuiz(event)">
          <fieldset class="quiz-question">
            <legend>1. Який протокол працює на транспортному рівні моделі OSI?</legend>
            <label><input type="radio" name="q1" value="tcp"> TCP</label>
            <label><input type="radio" name="q1" value="http"> HTTP</label>
            <label><input type="radio" name="q1" value="dns"> DNS</label>
          </fieldset>

          <fieldset class="quiz-question">
            <legend>2. Яке ключове слово визначає асинхронну функцію в Python?</legend>
            <label><input type="radio" name="q2" value="async"> async def</label>
            <label><input type="radio" name="q2" value="yield"> yield def</label>
          </fieldset>

          <div class="quiz-question">
            <h3>3. Оберіть стандартний порт HTTPS:</h3>
            <select name="q3_port">
              <option value="">Оберіть...</option>
              <option value="80">80</option>
              <option value="443">443</option>
            </select>
          </div>

          <button id="submit-btn" type="submit">Завершити тест</button>
        </form>
      `;
    }

    function submitQuiz(e) {
      e.preventDefault();
      let score = 0;
      const q1 = document.querySelector('input[name="q1"]:checked');
      const q2 = document.querySelector('input[name="q2"]:checked');
      const q3 = document.querySelector('select[name="q3_port"]');
      if (q1 && q1.value === 'tcp') score++;
      if (q2 && q2.value === 'async') score++;
      if (q3 && q3.value === '443') score++;
      document.getElementById('app').innerHTML = `
        <div id="result-screen">
          <h2>Тест завершено!</h2>
          <p>Ваш результат: ${score} / 3</p>
          <p>Правильних відповідей: ${score} з 3</p>
        </div>
      `;
    }
  </script>
</body>
</html>"""

        async def run_e2e():
            with tempfile.TemporaryDirectory() as tmp:
                html_path = Path(tmp) / "quiz_fixture.html"
                html_path.write_text(html_fixture, encoding="utf-8")
                file_url = html_path.as_uri()

                orig_cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    async with async_playwright() as p:
                        browser = await p.chromium.launch(headless=True)
                        try:
                            page = await browser.new_page()
                            controller = BrowserController(page, screenshots_dir=str(Path(tmp) / "screenshots"))

                            # Smart mock LLM that verifies it is ONLY called after questions are extracted
                            mock_llm = MagicMock()

                            async def fake_plan_test(state, user_data, extracted_questions=None):
                                self.assertIsNotNone(extracted_questions)
                                self.assertEqual(len(extracted_questions), 3)
                                selections = []
                                for q in extracted_questions:
                                    if q.question_number == 1:
                                        opt = next(o for o in q.options if "TCP" in o.text)
                                        selections.append(BatchSelection(question_number=1, element_id=opt.element_id, reason="TCP"))
                                    elif q.question_number == 2:
                                        opt = next(o for o in q.options if "async" in o.text)
                                        selections.append(BatchSelection(question_number=2, element_id=opt.element_id, reason="async def"))
                                    elif q.question_number == 3:
                                        opt = next(o for o in q.options if o.value == "443")
                                        selections.append(
                                            BatchSelection(
                                                question_number=3,
                                                element_id=opt.element_id,
                                                option_value="443",
                                                reason="HTTPS port 443",
                                            )
                                        )
                                return BatchPlan(
                                    selections=selections,
                                    total_questions=3,
                                    reason="Solved all 3 questions",
                                )

                            mock_llm.plan_test = AsyncMock(side_effect=fake_plan_test)

                            with (
                                patch.object(settings, "test_url", file_url),
                                patch.object(settings, "user_surname", "Шевченко"),
                                patch.object(settings, "user_name", "Тарас"),
                                patch.object(settings, "auto_submit", True),
                            ):
                                agent = BrowserAgent(controller, mock_llm)
                                result = await agent.run()

                                self.assertTrue(result)
                                # LLM called exactly once (only AFTER landing page start button was clicked!)
                                self.assertEqual(mock_llm.plan_test.await_count, 1)
                                self.assertEqual(agent.state, WorkflowState.COMPLETED)
                                self.assertEqual(agent.run_record["status"], "completed")
                                self.assertEqual(agent.run_record["test_outcome"], "score_captured")
                                self.assertEqual(agent.run_record["score"], "3/3")
                                self.assertEqual(agent.run_record["questions_detected"], 3)
                                self.assertEqual(agent.run_record["answers_planned"], 3)
                                self.assertEqual(agent.run_record["answers_selected"], 3)
                                self.assertEqual(agent.run_record["answers_failed"], 0)
                                self.assertEqual(agent.run_record["questions_unanswered"], 0)
                                self.assertTrue(agent.run_record["submission_attempted"])
                                self.assertTrue(agent.run_record["submission_confirmed"])
                        finally:
                            await browser.close()
                finally:
                    os.chdir(orig_cwd)

        asyncio.run(run_e2e())


class ManualSubmissionOrchestratorStateTests(unittest.TestCase):
    def test_manual_handoff_is_persisted_as_unconfirmed_not_completed(self):
        async def run_test():
            orchestrator = QuizOrchestrator(MagicMock(), MagicMock())
            with tempfile.TemporaryDirectory() as tmp:
                original_cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    result = await orchestrator._record_manual_submission_required()
                    self.assertFalse(result)
                    self.assertEqual(orchestrator.run_record["status"], "error")
                    self.assertEqual(
                        orchestrator.run_record["test_outcome"],
                        "awaiting_manual_submission",
                    )
                    self.assertEqual(
                        orchestrator.state,
                        WorkflowState.SUBMISSION_UNCONFIRMED,
                    )
                    self.assertFalse(orchestrator.run_record["submission_attempted"])
                    self.assertFalse(orchestrator.run_record["submission_confirmed"])
                    with open("results/agent_history.json", encoding="utf-8") as handle:
                        history = json.load(handle)
                    self.assertEqual(len(history), 1)
                    self.assertEqual(history[0]["status"], "error")
                finally:
                    os.chdir(original_cwd)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
