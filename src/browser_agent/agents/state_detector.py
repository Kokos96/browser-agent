import re
from typing import Any, Dict, List, Optional, Union

from ..models import (
    PageCategory,
    PageClassification,
    PageState,
    QuestionExtractionResult,
)
from .navigation_agent import NavigationAndLaunchAgent
from .question_extractor import QuizQuestionExtractor


class QuizPageStateDetector:
    """Agent C: Deterministically classifies page state before any LLM call."""

    LOADING_PHRASES = (
        "application loading",
        "render - application loading",
        "loading...",
        "завантаження...",
        "загрузка...",
        "please wait",
        "зачекайте",
    )

    CAPTCHA_PHRASES = (
        "verify you are human",
        "cloudflare",
        "turnstile",
        "g-recaptcha",
        "hcaptcha",
        "підтвердіть, що ви людина",
        "подтвердите, что вы не робот",
    )

    AUTH_PHRASES = (
        "sign in to continue",
        "log in to continue",
        "увійдіть в акаунт",
        "войдите в аккаунт",
        "authentication required",
    )

    ACCESS_GATE_PHRASES = (
        "subscription required",
        "subscribe to continue",
        "subscribe to access",
        "premium members only",
        "members only",
        "upgrade to continue",
        "upgrade your plan to continue",
        "purchase access",
        "pay to continue",
        "unlock this test",
        "unlock this quiz",
        "available to subscribers",
        "потрібна підписка",
        "доступно за підпискою",
        "оформіть підписку",
        "платний тест",
        "платна підписка",
        "требуется подписка",
        "доступно по подписке",
        "оформите подписку",
        "платный тест",
        "платная подписка",
        "оплатите доступ",
    )

    RESULT_PHRASES = (
        "ваш результат",
        "результат тесту",
        "правильних відповідей",
        "правильных ответов",
        "your score",
        "test score",
        "you scored",
        "quiz completed",
        "тест завершено",
        "тест пройдено",
        "дякуємо за проходження",
    )

    def __init__(
        self,
        navigation_agent: Optional[NavigationAndLaunchAgent] = None,
        question_extractor: Optional[QuizQuestionExtractor] = None,
    ):
        self.navigation_agent = navigation_agent or NavigationAndLaunchAgent()
        self.question_extractor = question_extractor or QuizQuestionExtractor()

    def is_loading(self, state: Union[PageState, Dict[str, Any]]) -> bool:
        ps = PageState.from_any(state)
        combined = f"{ps.title} {ps.text[:1500]}".lower()
        return any(phrase in combined for phrase in self.LOADING_PHRASES)

    def classify(
        self,
        state: Union[PageState, Dict[str, Any]],
        extraction: Optional[QuestionExtractionResult] = None,
    ) -> PageClassification:
        ps = PageState.from_any(state)
        title_lower = (ps.title or "").lower()
        text_lower = (ps.text or "").lower()
        combined_lower = f"{title_lower}\n{text_lower[:5000]}"

        # 1. Loading check
        if self.is_loading(ps):
            return PageClassification(
                category=PageCategory.LOADING_STATE,
                confidence=0.95,
                evidence=["loading_indicator_text_detected"],
                recommended_action="wait_for_page_load",
            )

        # 2. CAPTCHA check
        if any(p in combined_lower for p in self.CAPTCHA_PHRASES):
            return PageClassification(
                category=PageCategory.CAPTCHA_REQUIRED,
                confidence=0.95,
                evidence=["captcha_keyword_detected"],
                recommended_action="abort_captcha_required",
            )

        # 3. Extract structured questions & start controls
        ext = extraction or self.question_extractor.extract(ps)
        start_candidates = self.navigation_agent.rank_start_controls(ps.elements)

        # 4. Active Quiz check (ALWAYS takes precedence if real answerable questions exist)
        if len(ext.questions) > 0:
            evidence = [
                f"extracted_questions:{len(ext.questions)}",
            ]
            if ext.submit_control_id is not None:
                evidence.append(f"submit_control:{ext.submit_control_id}")
            if ext.next_control_id is not None:
                evidence.append(f"next_control:{ext.next_control_id}")
            return PageClassification(
                category=PageCategory.ACTIVE_QUIZ,
                confidence=0.95,
                evidence=evidence,
                recommended_action="extract_and_plan_answers",
                detected_questions_count=len(ext.questions),
                start_candidates=start_candidates,
            )

        # 5. Result page check (when 0 active questions remain)
        if any(p in combined_lower for p in self.RESULT_PHRASES):
            return PageClassification(
                category=PageCategory.RESULT_PAGE,
                confidence=0.90,
                evidence=["result_phrase_detected", "zero_active_questions"],
                recommended_action="extract_final_score",
                detected_questions_count=0,
            )

        # 6. Auth / Access gate check
        has_password_input = any((el.type or "").lower() == "password" and el.visible for el in ps.elements)
        if has_password_input or any(p in combined_lower for p in self.AUTH_PHRASES):
            return PageClassification(
                category=PageCategory.AUTH_REQUIRED,
                confidence=0.90,
                evidence=["auth_or_password_gate_detected"],
                recommended_action="abort_auth_required",
            )

        # 7. Paywall/subscription checks precede start-control detection so an
        # upsell page is not mistaken for a quiz landing page.
        if any(p in combined_lower for p in self.ACCESS_GATE_PHRASES):
            return PageClassification(
                category=PageCategory.ACCESS_GATE,
                confidence=0.92,
                evidence=["subscription_or_paywall_phrase_detected"],
                recommended_action="stop_access_required",
            )

        # 8. Landing / Start Page check
        has_profile_fields = any(
            (el.name or "").lower() in {"surname", "name", "grp", "group"}
            for el in ps.elements
        )
        if start_candidates or has_profile_fields:
            ev = []
            if start_candidates:
                ev.append(f"start_control_candidate:{start_candidates[0].element_id}:{start_candidates[0].text}")
            if has_profile_fields:
                ev.append("profile_bootstrap_fields_present")
            ev.append("zero_active_questions_before_start")
            return PageClassification(
                category=PageCategory.LANDING_PAGE,
                confidence=start_candidates[0].score if start_candidates else 0.85,
                evidence=ev,
                recommended_action="fill_profile_and_click_start",
                detected_questions_count=0,
                start_candidates=start_candidates,
            )

        # 9. Unknown / Non-Quiz Page (e.g. generic page with 46 links/buttons but no quiz controls)
        return PageClassification(
            category=PageCategory.UNKNOWN_PAGE,
            confidence=0.80,
            evidence=[
                f"interactive_elements:{len(ps.elements)}",
                "zero_extracted_questions",
                "no_start_control_candidates",
            ],
            recommended_action="fail_questions_not_detected",
            detected_questions_count=0,
        )
