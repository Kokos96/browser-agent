import json
import os

from browser import BrowserController
from llm import GeminiClient
from config import settings


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
            "group": settings.user_group
        }

        self.history = []

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

            self.history.append({
                "step": step,
                "state": state,
                "action": action.model_dump()
            })

            should_stop = await self.execute(
                action
            )

            if should_stop:
                break

        await self.save_history()

    async def execute(
        self,
        action
    ):

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

            return False

        if action.action == "select":

            if action.element_id is None:
                raise ValueError(
                    "select requires element_id"
                )

            await self.browser.select(
                action.element_id
            )

            return False

        if action.action == "wait":

            await self.browser.wait()

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