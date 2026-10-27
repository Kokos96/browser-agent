import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from playwright.async_api import Error as PlaywrightError

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from browser_agent.agent import BrowserAgent
from browser_agent.browser import BrowserController


class StartControlDetectionTests(unittest.TestCase):
    def test_finds_russian_start_test_link(self):
        control = {"id": 4, "tag": "a", "text": "Начать тест"}

        self.assertIs(
            BrowserAgent.find_start_button([control]),
            control,
        )

    def test_finds_accessible_role_button_with_english_label(self):
        control = {
            "id": 8,
            "tag": "div",
            "role": "button",
            "text": "",
            "aria_label": "Start quiz",
        }

        self.assertIs(
            BrowserAgent.find_start_button([control]),
            control,
        )

    def test_finds_ukrainian_start_test_button(self):
        control = {"id": 2, "tag": "button", "text": "Почати тест"}

        self.assertIs(
            BrowserAgent.find_start_button([control]),
            control,
        )

    def test_does_not_confuse_restart_with_start(self):
        control = {"id": 6, "tag": "button", "text": "Restart test"}

        self.assertIsNone(BrowserAgent.find_start_button([control]))

    def test_ignores_noninteractive_text(self):
        control = {"id": 1, "tag": "div", "text": "Начать тест"}

        self.assertIsNone(BrowserAgent.find_start_button([control]))


class BrowserControllerOverlayRetryTests(unittest.TestCase):
    def test_element_locator_uses_dom_marker_instead_of_nth_index(self):
        with tempfile.TemporaryDirectory() as directory:
            page = MagicMock()
            controller = BrowserController(page, screenshots_dir=directory)

            controller._element(7)

            page.locator.assert_called_once_with(
                '[data-browser-agent-element-id="7"]'
            )

    def test_click_retries_after_escape_dismisses_overlay(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as directory:
                page = MagicMock()
                target = MagicMock()
                target.scroll_into_view_if_needed = AsyncMock()
                target.click = AsyncMock(
                    side_effect=[
                        PlaywrightError(
                            "another element intercepts pointer events"
                        ),
                        None,
                    ]
                )
                page.locator.return_value = target
                page.keyboard.press = AsyncMock()
                page.wait_for_timeout = AsyncMock()

                controller = BrowserController(page, screenshots_dir=directory)
                await controller.click(5)

                self.assertEqual(target.click.await_count, 2)
                page.keyboard.press.assert_awaited_once_with("Escape")
                self.assertEqual(
                    target.scroll_into_view_if_needed.await_count,
                    2,
                )

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
