import os

from playwright.async_api import Page


class BrowserController:
    SELECTOR = (
        "input, button, textarea, select, a, img, "
        "[role='button'], [role='radio'], [role='checkbox']"
    )

    def __init__(
        self,
        page: Page,
        screenshots_dir: str = "screenshots"
    ):
        self.page = page
        self.screenshots_dir = screenshots_dir

        os.makedirs(self.screenshots_dir, exist_ok=True)

    async def open(self, url: str):
        await self.page.goto(
            url,
            wait_until="domcontentloaded"
        )

    async def inspect(self):
        locator = self.page.locator(self.SELECTOR)

        elements = await locator.evaluate_all(
            """
            elements => elements.map((element, index) => ({
                id: index,

                tag: element.tagName
                    ? element.tagName.toLowerCase()
                    : "",

                type: element.getAttribute("type"),

                name: element.getAttribute("name"),

                text: (
                    element.innerText ||
                    element.textContent ||
                    ""
                ).trim().slice(0, 1000),

                placeholder: element.getAttribute("placeholder"),

                // Дуже важливо:
                // беремо реальне поточне значення DOM-елемента.
                value:
                    "value" in element
                        ? element.value
                        : element.getAttribute("value"),

                checked:
                    "checked" in element
                        ? Boolean(element.checked)
                        : null,

                aria_label:
                    element.getAttribute("aria-label"),

                html_id:
                    element.getAttribute("id")
            }))
            """
        )

        body_text = await self.page.locator("body").inner_text()

        return {
            "url": self.page.url,
            "title": await self.page.title(),
            "text": body_text[:20000],
            "elements": elements,
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
        element = self._element(element_id)

        await element.scroll_into_view_if_needed()
        await element.click()

    async def fill(
        self,
        element_id: int,
        value: str
    ):
        element = self._element(element_id)

        await element.scroll_into_view_if_needed()
        await element.fill(value)

    async def select(
        self,
        element_id: int,
        value: str | None = None
    ):
        element = self._element(element_id)

        tag = await element.evaluate(
            "element => element.tagName.toLowerCase()"
        )

        if tag == "select":
            if value is None:
                raise ValueError(
                    "select element requires a value"
                )

            await element.select_option(value)
            return

        element_type = await element.get_attribute("type")

        if element_type in {"radio", "checkbox"}:
            await element.check()
            return

        await element.click()

    async def wait(self, milliseconds: int = 1000):
        await self.page.wait_for_timeout(milliseconds)

    async def current_url(self):
        return self.page.url

    async def wait_for_navigation(self):
        try:
            await self.page.wait_for_load_state(
                "domcontentloaded",
                timeout=5000
            )
        except Exception:
            pass

    def _element(self, element_id: int):
        locator = self.page.locator(self.SELECTOR)

        return locator.nth(element_id)