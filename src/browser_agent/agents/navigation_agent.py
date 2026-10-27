import re
from typing import Any, Dict, List, Optional, Sequence, Union

from ..browser import BrowserController
from ..models import Element, PageState, StartControlCandidate


class NavigationAndLaunchAgent:
    """Agent B: Detects profile fields, ranks start-test controls across UK/RU/EN, and verifies quiz launch."""

    START_PHRASES = (
        "розпочати тест",
        "почати тест",
        "почати опитування",
        "пройти тест",
        "запустити тест",
        "розпочати проходження",
        "почати проходження",
        "пройти вікторину",
        "почати вікторину",
        "начать тест",
        "старт теста",
        "пройти тестирование",
        "начать викторину",
        "начать прохождение",
        "пройти викторину",
        "начать опрос",
        "start the test",
        "start test",
        "start the quiz",
        "start quiz",
        "begin the test",
        "begin test",
        "begin the quiz",
        "begin quiz",
        "take the test",
        "take this quiz",
        "take the quiz",
        "take quiz",
        "play the quiz",
        "play quiz",
        "start assessment",
        "start the assessment",
        "start questionnaire",
        "launch test",
        "launch quiz",
    )

    EXACT_SHORT_LABELS = {
        "розпочати",
        "почати",
        "начать",
        "старт",
        "start",
        "begin",
    }

    NEGATIVE_PATTERNS = (
        r"\brestart\b",
        r"\bretry\b",
        r"\btry again\b",
        r"спочатку",
        r"ще раз",
        r"заново",
        r"знову",
        r"повторно",
        r"\bshare\b",
        r"поділитися",
        r"поделиться",
        r"\bprint\b",
        r"\blogin\b",
        r"\bsign in\b",
        r"\bsign up\b",
        r"\bregister\b",
        r"\bsubscribe\b",
        r"увійти",
        r"реєстрація",
        r"войти",
    )

    @classmethod
    def rank_start_controls(
        cls,
        elements: Sequence[Union[Element, Dict[str, Any]]],
    ) -> List[StartControlCandidate]:
        candidates: List[StartControlCandidate] = []

        for raw in elements:
            el = Element.from_any(raw)
            if el.disabled or not el.visible:
                continue

            tag = (el.tag or "").strip().lower()
            role = (el.role or "").strip().lower()
            input_type = (el.type or "").strip().lower()

            is_clickable = (
                tag in {"button", "a"}
                or role == "button"
                or (tag == "input" and input_type in {"button", "submit"})
            )
            if not is_clickable:
                continue

            visible_text = re.sub(
                r"\s+",
                " ",
                " ".join(
                    str(val or "")
                    for val in (el.text, el.aria_label, el.title, el.value)
                ),
            ).strip()
            lower_text = visible_text.lower()
            if not lower_text:
                continue

            if any(re.search(pat, lower_text) for pat in cls.NEGATIVE_PATTERNS):
                continue

            score = 0.0
            reasons: List[str] = []

            for phrase in cls.START_PHRASES:
                if re.search(rf"\b{re.escape(phrase)}\b", lower_text):
                    score += 0.85
                    reasons.append(f"matched_phrase:{phrase}")
                    break

            if score == 0.0 and lower_text in cls.EXACT_SHORT_LABELS:
                score += 0.72
                reasons.append(f"exact_short_label:{lower_text}")

            if score == 0.0:
                continue

            if tag == "button" or role == "button":
                score += 0.1
                reasons.append("button_semantics")
            if el.in_nav_or_footer:
                score -= 0.45
                reasons.append("in_nav_or_footer_penalty")

            href = (el.href or "").lower()
            if tag == "a" and href and any(x in href for x in ("facebook.com", "t.me", "twitter.com", "mailto:")):
                continue

            score = max(0.0, min(1.0, round(score, 3)))
            if score >= 0.4:
                candidates.append(
                    StartControlCandidate(
                        element_id=el.id,
                        score=score,
                        text=visible_text[:120],
                        tag=tag,
                        role=el.role,
                        reasons=reasons,
                    )
                )

        candidates.sort(key=lambda c: (-c.score, c.element_id))
        return candidates

    @classmethod
    def find_start_button(
        cls,
        elements: Sequence[Union[Element, Dict[str, Any]]],
    ) -> Optional[Union[Element, Dict[str, Any]]]:
        """Return the highest-ranked start control element (preserving original dict/Element reference)."""
        ranked = cls.rank_start_controls(elements)
        if not ranked:
            return None
        best_id = ranked[0].element_id
        for item in elements:
            item_id = item.id if isinstance(item, Element) else item.get("id")
            if item_id == best_id:
                return item
        return None

    @staticmethod
    def find_by_name(
        elements: Sequence[Union[Element, Dict[str, Any]]],
        name: str,
    ) -> Optional[Union[Element, Dict[str, Any]]]:
        target = name.lower()
        for element in elements:
            el_name = (
                element.name if isinstance(element, Element) else element.get("name")
            ) or ""
            if el_name.lower() == target:
                return element
        return None

    async def fill_optional_profile_fields(
        self,
        browser: BrowserController,
        state: Union[PageState, Dict[str, Any]],
        user_data: Dict[str, str],
    ) -> List[str]:
        elements = state.elements if isinstance(state, PageState) else state.get("elements", [])
        filled: List[str] = []

        field_mappings = (
            ("surname", user_data.get("surname", "")),
            ("name", user_data.get("name", "")),
            ("grp", user_data.get("group", "")),
            ("group", user_data.get("group", "")),
        )

        for field_name, field_val in field_mappings:
            if not field_val:
                continue
            element = self.find_by_name(elements, field_name)
            if not element:
                continue
            el_obj = Element.from_any(element)
            current_val = (el_obj.value or "").strip()
            if not current_val:
                print(f"Browser: filling {field_name}")
                await browser.fill(el_obj.id, field_val)
                await browser.wait(200)
                filled.append(field_name)

        return filled

    async def launch_quiz(
        self,
        browser: BrowserController,
        state: Union[PageState, Dict[str, Any]],
        user_data: Dict[str, str],
        wait_ms: int = 1000,
    ) -> Dict[str, Any]:
        ps = PageState.from_any(state)
        raw_elements = state.elements if isinstance(state, PageState) else state.get("elements", [])

        filled_fields = await self.fill_optional_profile_fields(browser, state, user_data)
        if filled_fields:
            refreshed = await browser.inspect()
            ps = PageState.from_any(refreshed)
            raw_elements = refreshed.get("elements", [])

        candidates = self.rank_start_controls(raw_elements)
        if not candidates:
            return {
                "start_clicked": False,
                "filled_fields": filled_fields,
                "start_candidate": None,
                "transition_verified": False,
            }

        best = candidates[0]
        print(f"Browser: starting test (element {best.element_id}: {best.text})")
        await browser.click(best.element_id)
        new_state_dict = await browser.wait_for_transition(
            previous_fingerprint=ps.dom_fingerprint,
            timeout_ms=wait_ms,
        )
        new_ps = PageState.from_any(new_state_dict)
        transition_verified = (
            new_ps.dom_fingerprint != ps.dom_fingerprint
            or new_ps.url != ps.url
            or len(new_ps.elements) != len(ps.elements)
        )

        return {
            "start_clicked": True,
            "filled_fields": filled_fields,
            "start_candidate": best.model_dump(),
            "transition_verified": transition_verified,
            "post_launch_state": new_state_dict,
        }
