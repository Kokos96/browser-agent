import asyncio

from playwright.async_api import async_playwright

from agent import BrowserAgent
from browser import BrowserController
from llm import GeminiClient
from config import settings


async def main():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=settings.headless
        )

        page = await browser.new_page()

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