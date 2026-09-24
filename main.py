import asyncio

from playwright.async_api import async_playwright

from agent import BrowserAgent


URL = "https://quiz-web-wzr7.onrender.com/"


async def main():
    async with async_playwright() as p:
        # Запускаємо браузер
        browser = await p.chromium.launch(
            headless=False
        )

        # Створюємо сторінку
        page = await browser.new_page()

        # Відкриваємо тест
        await page.goto(
            URL,
            wait_until="domcontentloaded"
        )

        # Створюємо агента
        agent = BrowserAgent(page)

        # Аналізуємо початкову сторінку
        state = await agent.inspect_page()

        print("URL:")
        print(state["url"])

        print("\nTitle:")
        print(state["title"])

        print("\nButtons:")
        for button in state["buttons"]:
            print("-", button)

        print("\nInputs:")
        for input_data in state["inputs"]:
            print("-", input_data)

        # Заповнюємо тестові дані
        await agent.fill_user_data(
            surname="Чорний",
            name="Костянтин",
            group="ФеП-22"
        )

        print("\nДані користувача заповнено")

        # Запускаємо тест
        await agent.start_test()

        print("Тест запущено")

        # Отримуємо новий стан сторінки
        state = await agent.inspect_page()

        print("\nНовий стан сторінки:")
        print("-" * 50)
        print(state["text"][:5000])
        print("-" * 50)

        # Залишаємо браузер відкритим
        await asyncio.to_thread(
            input,
            "\nНатисніть Enter для завершення..."
        )

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())