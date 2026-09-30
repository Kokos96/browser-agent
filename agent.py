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

    async def run(self):

        print("Agent started")

        print(
            "URL:",
            settings.test_url
        )

        await self.browser.open(
            settings.test_url
        )

        # ---------------------------------------------
        # STEP 1:
        # Bootstrap form.
        # Gemini is NOT used.
        # ---------------------------------------------

        for step in range(
            1,
            settings.max_steps + 1
        ):

            print()
            print("=" * 60)
            print(
                f"BOOTSTRAP STEP {step}"
            )
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

            if self.is_loading(
                state
            ):

                print(
                    "Browser: waiting for Render..."
                )

                await self.browser.wait(
                    1500
                )

                continue

            if await self.bootstrap(
                state
            ):

                continue

            break

        # ---------------------------------------------
        # STEP 2:
        # Wait for actual test.
        # Gemini is NOT used.
        # ---------------------------------------------

        print()
        print("=" * 60)
        print("WAITING FOR TEST")
        print("=" * 60)

        state = await self.browser.inspect()

        if self.is_empty_test_page(
            state
        ):

            print(
                "Browser: /start is still loading."
            )

            state = (
                await self.browser.wait_for_test(
                    settings.test_load_timeout
                )
            )

        print(
            "Test URL:",
            state["url"]
        )

        print(
            "Interactive elements:",
            len(state["elements"])
        )

        print(
            "Page text length:",
            len(state["text"])
        )

        # ---------------------------------------------
        # STEP 3:
        # One Gemini request for the entire test.
        # ---------------------------------------------

        if self.is_empty_test_page(
            state
        ):

            self.save_debug_state(
                state
            )

            print(
                "ERROR: test content did not load."
            )

            await self.save_history()

            return

        screenshot_path = None

        if settings.enable_vision:

            screenshot_path = (
                await self.browser.screenshot(
                    999
                )
            )

        state[
            "screenshot_path"
        ] = screenshot_path

        # ---------------------------------------------
        # Print useful diagnostics.
        # ---------------------------------------------

        radio_count = 0
        checkbox_count = 0

        for element in state[
            "elements"
        ]:

            element_type = (
                element.get("type")
                or ""
            ).lower()

            if element_type == "radio":
                radio_count += 1

            if element_type == "checkbox":
                checkbox_count += 1

        print()
        print(
            "Radio controls:",
            radio_count
        )

        print(
            "Checkbox controls:",
            checkbox_count
        )

        print()
        print(
            "AI: ONE request for the complete test."
        )

        try:

            plan = await self.llm.plan_test(
                state,
                self.user_data
            )

        except RuntimeError as error:

            print()
            print(
                "AI STOP:",
                error
            )

            self.history.append(
                {
                    "error": str(error),
                    "state": state,
                }
            )

            await self.save_history()

            return

        # ---------------------------------------------
        # Validate the returned plan.
        # ---------------------------------------------

        valid_ids = {
            element["id"]
            for element in state[
                "elements"
            ]
        }

        valid_selections = []

        for selection in plan.selections:

            if (
                selection.element_id
                not in valid_ids
            ):

                print(
                    "WARNING: Gemini returned "
                    "unknown element:",
                    selection.element_id
                )

                continue

            valid_selections.append(
                selection
            )

        print()
        print(
            "Valid selections:",
            len(valid_selections)
        )

        print(
            "Gemini reported questions:",
            plan.total_questions
        )

        # ---------------------------------------------
        # Execute all selections.
        # No additional Gemini calls.
        # ---------------------------------------------

        for selection in valid_selections:

            print(
                f"Question "
                f"{selection.question_number}: "
                f"select element "
                f"{selection.element_id}"
            )

            try:

                await self.browser.select(
                    selection.element_id
                )

                self.history.append(
                    {
                        "question":
                            selection.question_number,

                        "element_id":
                            selection.element_id,

                        "reason":
                            selection.reason,
                    }
                )

            except Exception as error:

                print(
                    "Selection error:",
                    error
                )

        # ---------------------------------------------
        # Finished selecting answers.
        # ---------------------------------------------

        print()
        print("=" * 60)
        print("ALL PLANNED ANSWERS EXECUTED")
        print("=" * 60)

        # ---------------------------------------------
        # Look for final submit button.
        # ---------------------------------------------

        final_state = (
            await self.browser.inspect()
        )

        submit_id = (
            self.find_submit_button(
                final_state
            )
        )

        if submit_id is not None:

            print(
                "Final button found:",
                submit_id
            )

            if settings.auto_submit:

                print(
                    "Browser: submitting test..."
                )

                await self.browser.click(
                    submit_id
                )

                await self.browser.wait(
                    1500
                )

                print(
                    "Browser: test submitted."
                )

            else:

                print(
                    "AUTO_SUBMIT=false"
                )

                print(
                    "Test answers are selected. "
                    "Submit manually."
                )

        else:

            print(
                "No final submit button detected."
            )

        await self.save_history()

    def is_loading(
        self,
        state: dict
    ) -> bool:

        title = (
            state["title"] or ""
        ).lower()

        text = (
            state["text"] or ""
        ).lower()

        phrases = [
            "application loading",
            "render - application loading",
            "loading...",
        ]

        for phrase in phrases:

            if phrase in title:
                return True

            if phrase in text:
                return True

        return False

    def is_empty_test_page(
        self,
        state: dict
    ) -> bool:

        url = (
            state["url"] or ""
        ).lower()

        elements = state[
            "elements"
        ]

        text = (
            state["text"] or ""
        ).strip()

        if (
            url.rstrip("/").endswith("/start")
            and not elements
        ):

            return True

        if (
            not elements
            and len(text) < 150
        ):

            return True

        return False

    async def bootstrap(
        self,
        state: dict
    ) -> bool:

        elements = state[
            "elements"
        ]

        # ---------------------------------------------
        # Surname
        # ---------------------------------------------

        element = self.find_by_name(
            elements,
            "surname"
        )

        if element:

            value = (
                element.get("value")
                or ""
            ).strip()

            if (
                not value
                and self.user_data[
                    "surname"
                ]
            ):

                print(
                    "Browser: filling surname"
                )

                await self.browser.fill(
                    element["id"],
                    self.user_data[
                        "surname"
                    ]
                )

                await self.browser.wait(
                    200
                )

                return True

        # ---------------------------------------------
        # Name
        # ---------------------------------------------

        element = self.find_by_name(
            elements,
            "name"
        )

        if element:

            value = (
                element.get("value")
                or ""
            ).strip()

            if (
                not value
                and self.user_data[
                    "name"
                ]
            ):

                print(
                    "Browser: filling name"
                )

                await self.browser.fill(
                    element["id"],
                    self.user_data[
                        "name"
                    ]
                )

                await self.browser.wait(
                    200
                )

                return True

        # ---------------------------------------------
        # Group
        # ---------------------------------------------

        element = self.find_by_name(
            elements,
            "grp"
        )

        if element:

            value = (
                element.get("value")
                or ""
            ).strip()

            if (
                not value
                and self.user_data[
                    "group"
                ]
            ):

                print(
                    "Browser: filling group"
                )

                await self.browser.fill(
                    element["id"],
                    self.user_data[
                        "group"
                    ]
                )

                await self.browser.wait(
                    200
                )

                return True

        # ---------------------------------------------
        # Start test
        # ---------------------------------------------

        for element in elements:

            tag = (
                element.get("tag")
                or ""
            ).lower()

            text = (
                element.get("text")
                or ""
            ).strip().lower()

            if tag != "button":
                continue

            phrases = [
                "почати тест",
                "почати",
                "start test",
                "start",
            ]

            if any(
                phrase in text
                for phrase in phrases
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
    def find_by_name(
        elements: list[dict],
        name: str
    ) -> dict | None:

        for element in elements:

            element_name = (
                element.get("name")
                or ""
            ).lower()

            if (
                element_name
                == name.lower()
            ):

                return element

        return None

    @staticmethod
    def find_submit_button(
        state: dict
    ) -> int | None:

        phrases = [
            "завершити тест",
            "завершити",
            "відправити",
            "відправити відповіді",
            "закінчити тест",
            "submit",
            "finish test",
            "finish",
        ]

        for element in state[
            "elements"
        ]:

            tag = (
                element.get("tag")
                or ""
            ).lower()

            text = (
                element.get("text")
                or ""
            ).strip().lower()

            if tag != "button":
                continue

            if any(
                phrase in text
                for phrase in phrases
            ):

                return element["id"]

        return None

    def save_debug_state(
        self,
        state: dict
    ):

        os.makedirs(
            "results",
            exist_ok=True
        )

        with open(
            "results/debug_state.json",
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                state,
                file,
                ensure_ascii=False,
                indent=2,
                default=str
            )

        print(
            "Debug state saved:"
            " results/debug_state.json"
        )

    async def save_history(
        self
    ):

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