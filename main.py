"""Worker entry point for Browser Automation Agent."""

import asyncio
import sys
from pathlib import Path

# Support running directly from the repository checkout and in the Cloud Run image.
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from playwright.async_api import async_playwright

from browser_agent.agent import BrowserAgent
from browser_agent.browser import BrowserController
from browser_agent.config import settings
from browser_agent.llm import GeminiClient


async def main() -> bool:
    """Run one browser session; return False when the agent reports a failed run."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=settings.headless)
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            browser_controller = BrowserController(page)
            agent = BrowserAgent(browser_controller, GeminiClient())
            return await agent.run()
        finally:
            await browser.close()


def exit_code_for_run_result(result: bool | None) -> int:
    """Map an explicit successful result to 0; failure or missing result to 1."""
    return 0 if result is True else 1


if __name__ == "__main__":
    raise SystemExit(exit_code_for_run_result(asyncio.run(main())))
