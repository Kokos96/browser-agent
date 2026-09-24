import json
import os

from browser import BrowserController
from config import settings
from llm import GeminiClient


class BrowserAgent:
    def __init__(
        self,
        browser: BrowserController,
        llm: GeminiClient
    ):
        self.browser = browser
        self.llm = llm

        self.user_data = {
            "surname": settings.user_surname,
            "name": settings.user_name,
            "group": settings.user_group,
        }

        self.history = []

        self.last_action = None
        self.same_action_count = 0

    async def run(self):
        print("Agent started")
        print("URL:", settings.test_url)

        await self.browser.open(
            settings.test_url
        )

        for step in range(
            1,
            settings.max_steps + 1
        ):
            print()
            print("=" * 60)
            print(f"STEP {step}")
            print("=" * 60)

            state = await self.browser.inspect()

            print(
                "Page:",
                state["title"]
            )

            print(
                "URL:",
                state["url"]
            )

            print(
                "Interactive elements:",
                len(state["elements"])
            )

            self.print_elements(
                state["elements"]
            )

            # -------------------------------------------------
            # 1. Чекаємо завантаження сайту.
            # Gemini тут НЕ використовується.
            # -------------------------------------------------

            if self.is_loading_page(state):
                print(
                    "Browser: application is loading."
                )

                await self.browser.wait(
                    1500
                )

                continue

            # -------------------------------------------------
            # 2. Виконуємо стартову форму.
            # Gemini тут НЕ використовується.
            # -------------------------------------------------

            bootstrap_result = (
                await self.bootstrap_test(
                    state
                )
            )

            if bootstrap_result:
                continue

            # -------------------------------------------------
            # 3. Тільки тепер потрібен AI.
            # -------------------------------------------------

            screenshot_path = None

            if settings.enable_vision:
                screenshot_path = (
                    await self.browser.screenshot(
                        step
                    )
                )

            state["screenshot_path"] = (
                screenshot_path
            )

            print()
            print(
                "AI: asking Gemini..."
            )

            action = await self.llm.decide(
                state,
                self.user_data
            )

            print(
                "Action:",
                action.action
            )

            print(
                "Element:",
                action.element_id
            )

            print(
                "Value:",
                action.value
            )

            print(
                "Reason:",
                action.reason
            )

            self.history.append(
                {
                    "step": step,
                    "state": state,
                    "action": action.model_dump(),
                }
            )

            if self.is_repeated_action(
                action
            ):
                print(
                    "Warning: repeated identical action."
                )

                if (
                    self.same_action_count
                    >= settings.max_same_action
                ):
                    print(
                        "Agent stopped because of "
                        "an action loop."
                    )
                    break

            should_stop = await self.execute(
                action
            )

            if should_stop:
                break

        await self.save_history()

    def print_elements(
        self,
        elements: list[dict]
    ):
        print()
        print("Elements:")

        for element in elements:
            value = element.get(
                "value"
            )

            text = element.get(
                "text"
            )

            name = element.get(
                "name"
            )

            element_id = element.get(
                "id"
            )

            tag = element.get(
                "tag"
            )

            print(
                f"  ID={element_id} "
                f"tag={tag} "
                f"name={name!r} "
                f"value={value!r} "
                f"text={text!r}"
            )

    def is_loading_page(
        self,
        state: dict
    ) -> bool:

        title = (
            state["title"] or ""
        ).lower()

        text = (
            state["text"] or ""
        ).lower()

        loading_titles = [
            "application loading",
            "render - application loading",
            "loading",
        ]

        for phrase in loading_titles:
            if phrase in title:
                return True

        loading_phrases = [
            "application loading",
            "loading...",
        ]

        for phrase in loading_phrases:
            if phrase in text:
                return True

        return False

    async def bootstrap_test(
        self,
        state: dict
    ) -> bool:

        elements = state["elements"]

        # -----------------------------------------------------
        # Surname
        # -----------------------------------------------------

        surname = self.find_input(
            elements,
            "surname"
        )

        if surname:
            current_value = (
                surname.get("value") or ""
            ).strip()

            if (
                not current_value
                and self.user_data["surname"]
            ):
                print(
                    "Browser: filling surname"
                )

                await self.browser.fill(
                    surname["id"],
                    self.user_data["surname"]
                )

                await self.browser.wait(
                    150
                )

                return True

        # -----------------------------------------------------
        # Name
        # -----------------------------------------------------

        name = self.find_input(
            elements,
            "name"
        )

        if name:
            current_value = (
                name.get("value") or ""
            ).strip()

            if (
                not current_value
                and self.user_data["name"]
            ):
                print(
                    "Browser: filling name"
                )

                await self.browser.fill(
                    name["id"],
                    self.user_data["name"]
                )

                await self.browser.wait(
                    150
                )

                return True

        # -----------------------------------------------------
        # Group
        # -----------------------------------------------------

        group = self.find_input(
            elements,
            "grp"
        )

        if group:
            current_value = (
                group.get("value") or ""
            ).strip()

            if (
                not current_value
                and self.user_data["group"]
            ):
                print(
                    "Browser: filling group"
                )

                await self.browser.fill(
                    group["id"],
                    self.user_data["group"]
                )

                await self.browser.wait(
                    150
                )

                return True

        # -----------------------------------------------------
        # Start button
        # -----------------------------------------------------

        for element in elements:
            tag = (
                element.get("tag") or ""
            ).lower()

            text = (
                element.get("text") or ""
            ).strip().lower()

            if tag != "button":
                continue

            start_phrases = [
                "почати тест",
                "почати",
                "start test",
                "start",
            ]

            if any(
                phrase in text
                for phrase in start_phrases
            ):
                print(
                    "Browser: starting test"
                )

                await self.browser.click(
                    element["id"]
                )

                await self.browser.wait(
                    1000
                )

                return True

        return False

    @staticmethod
    def find_input(
        elements: list[dict],
        name: str
    ) -> dict | None:

        for element in elements:
            element_name = (
                element.get("name") or ""
            ).lower()

            if element_name == name.lower():
                return element

        return None

    def is_repeated_action(
        self,
        action
    ) -> bool:

        current = (
            action.action,
            action.element_id,
            action.value
        )

        if current == self.last_action:
            self.same_action_count += 1
            return True

        self.last_action = current
        self.same_action_count = 1

        return False

    async def execute(
        self,
        action
    ) -> bool:

        if action.action == "fill":
            if action.element_id is None:
                raise ValueError(
                    "fill requires element_id"
                )

            if action.value is None:
                raise ValueError(
                    "fill requires value"
                )

            await self.browser.fill(
                action.element_id,
                action.value
            )

            return False

        if action.action == "click":
            if action.element_id is None:
                raise ValueError(
                    "click requires element_id"
                )

            await self.browser.click(
                action.element_id
            )

            await self.browser.wait(
                700
            )

            return False

        if action.action == "select":
            if action.element_id is None:
                raise ValueError(
                    "select requires element_id"
                )

            await self.browser.select(
                action.element_id,
                action.value
            )

            await self.browser.wait(
                200
            )

            return False

        if action.action == "wait":
            await self.browser.wait(
                1000
            )

            return False

        if action.action == "finish":
            print(
                "Agent finished."
            )

            return True

        if action.action == "stop":
            print(
                "Agent stopped."
            )

            return True

        print(
            "Unknown action."
        )

        return True

    async def save_history(self):
        os.makedirs(
            "results",
            exist_ok=True
        )

        path = (
            "results/agent_history.json"
        )

        with open(
            path,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                self.history,
                file,
                ensure_ascii=False,
                indent=2,
                default=str
            )

        print()
        print(
            "History saved:",
            path
        )