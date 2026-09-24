import os

from playwright.async_api import (
    Page,
    Browser,
)


class BrowserController:

    def __init__(
        self,
        page: Page,
        screenshots_dir: str = "screenshots"
    ):
        self.page = page
        self.screenshots_dir = screenshots_dir

        os.makedirs(
            screenshots_dir,
            exist_ok=True
        )

    async def open(self, url: str):
        await self.page.goto(
            url,
            wait_until="domcontentloaded"
        )

    async def inspect(self):
        elements = await self.page.locator(
            "input, button, textarea, select, "
            "a, img, [role='button'], "
            "[role='radio'], [role='checkbox']"
        ).evaluate_all(
            """
            elements => elements.map((element, index) => ({
                id: index,
                tag: element.tagName.toLowerCase(),
                type: element.getAttribute("type"),
                name: element.getAttribute("name"),
                text: (
                    element.innerText ||
                    element.textContent ||
                    ""
                ).trim().slice(0, 500),
                placeholder:
                    element.getAttribute("placeholder"),
                value:
                    element.getAttribute("value"),
                checked:
                    element.checked ?? null
            }))
            """
        )

        text = await self.page.locator(
            "body"
        ).inner_text()

        return {
            "url": self.page.url,
            "title": await self.page.title(),
            "text": text[:12000],
            "elements": elements
        }

    async def screenshot(self, step: int):
        path = os.path.join(
            self.screenshots_dir,
            f"step_{step:03d}.png"
        )

        await self.page.screenshot(
            path=path,
            full_page=True
        )

        return path

    async def click(self, element_id: int):
        locator = self._element(
            element_id
        )

        await locator.click()

    async def fill(
        self,
        element_id: int,
        value: str
    ):
        locator = self._element(
            element_id
        )

        await locator.fill(value)

    async def select(
        self,
        element_id: int
    ):
        locator = self._element(
            element_id
        )

        await locator.check()

    async def wait(self):
        await self.page.wait_for_timeout(
            1000
        )

    def _element(self, element_id: int):
        locator = self.page.locator(
            "input, button, textarea, select, "
            "a, img, [role='button'], "
            "[role='radio'], [role='checkbox']"
        )

        return locator.nth(element_id)