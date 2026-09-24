from playwright.async_api import Page


class BrowserAgent:
    def __init__(self, page: Page):
        self.page = page

    async def inspect_page(self):
        # Отримуємо всі кнопки на сторінці
        buttons = await self.page.locator("button").all_inner_texts()

        # Отримуємо всі текстові поля
        inputs = await self.page.locator(
            "input"
        ).evaluate_all("""
            elements => elements.map(element => ({
                type: element.type,
                name: element.name,
                placeholder: element.placeholder,
                value: element.value
            }))
        """)

        # Отримуємо заголовки
        headings = await self.page.locator(
            "h1, h2, h3"
        ).all_inner_texts()

        # Отримуємо текст сторінки
        text = await self.page.locator(
            "body"
        ).inner_text()

        return {
            "url": self.page.url,
            "title": await self.page.title(),
            "headings": headings,
            "buttons": buttons,
            "inputs": inputs,
            "text": text
        }