import asyncio
from playwright.async_api import async_playwright


# URL тестового сайту
URL = "https://quiz-web-wzr7.onrender.com/"


async def main():
    # Запускаємо Playwright
    async with async_playwright() as p:

        # Запускаємо Chromium у звичайному режимі,
        # щоб бачити дії агента
        browser = await p.chromium.launch(
            headless=False
        )

        # Створюємо нову вкладку браузера
        page = await browser.new_page()

        print("Відкриваю сайт...")

        # Переходимо на сторінку тесту
        await page.goto(
            URL,
            wait_until="domcontentloaded"
        )

        print("Сайт відкрито")
        print("URL:", page.url)

        # Отримуємо заголовок сторінки
        title = await page.title()

        print("Заголовок:", title)

        # Отримуємо весь видимий текст сторінки
        text = await page.locator("body").inner_text()

        print("\nВміст сторінки:")
        print("-" * 50)
        print(text[:5000])
        print("-" * 50)

        # Очікуємо натискання Enter перед закриттям браузера
        await asyncio.to_thread(
            input,
            "\nНатисніть Enter для завершення..."
        )

        # Закриваємо браузер
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())