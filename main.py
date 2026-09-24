import asyncio

from playwright.async_api import async_playwright

from agent import BrowserAgent


URL = "https://quiz-web-wzr7.onrender.com/"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False
        )

        page = await browser.new_page()

        await page.goto(
            URL,
            wait_until="domcontentloaded"
        )

        agent = BrowserAgent(page)

        # Агент аналізує поточну сторінку
        state = await agent.inspect_page()

        print("\nURL:")
        print(state["url"])

        print("\nTitle:")
        print(state["title"])

        print("\nHeadings:")
        for heading in state["headings"]:
            print("-", heading)

        print("\nButtons:")
        for button in state["buttons"]:
            print("-", button)

        print("\nInputs:")
        for input_data in state["inputs"]:
            print("-", input_data)

        print("\nPage text:")
        print("-" * 50)
        print(state["text"][:5000])
        print("-" * 50)

        await asyncio.to_thread(
            input,
            "\nНатисніть Enter для завершення..."
        )

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())