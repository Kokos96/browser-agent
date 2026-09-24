import asyncio

from playwright.async_api import async_playwright

from agent import BrowserAgent
from browser import BrowserController
from config import settings
from llm import GeminiClient


async def main():
    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=settings.headless
        )

        page = await browser.new_page(
            viewport={
                "width": 1440,
                "height": 900,
            }
        )

        browser_controller = BrowserController(
            page
        )

        llm = GeminiClient()

        agent = BrowserAgent(
            browser_controller,
            llm
        )

        try:
            await agent.run()

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())