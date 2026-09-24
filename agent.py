from playwright.async_api import Page


class BrowserAgent:
    def __init__(self, page: Page):
        self.page = page

    async def inspect_page(self):
        # Отримуємо всі кнопки на сторінці
        buttons = await self.page.locator(
            "button"
        ).all_inner_texts()

        # Отримуємо всі поля введення
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

    async def fill_user_data(
        self,
        surname: str,
        name: str,
        group: str
    ):
        # Заповнюємо прізвище
        await self.page.locator(
            'input[name="surname"]'
        ).fill(surname)

        # Заповнюємо ім'я
        await self.page.locator(
            'input[name="name"]'
        ).fill(name)

        # Заповнюємо групу
        await self.page.locator(
            'input[name="grp"]'
        ).fill(group)

    async def start_test(self):
        # Знаходимо кнопку початку тесту
        button = self.page.get_by_role(
            "button",
            name="Почати тест"
        )

        # Натискаємо кнопку
        await button.click()

        # Чекаємо, поки сторінка стабілізується
        await self.page.wait_for_load_state(
            "domcontentloaded"
        )