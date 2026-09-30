import os

from playwright.async_api import Page


class BrowserController:

    SELECTOR = (
        "input, button, textarea, select, a, img, "
        "[role='button'], [role='radio'], [role='checkbox'], "
        "[role='option']"
    )

    def __init__(
        self,
        page: Page,
        screenshots_dir: str = "screenshots"
    ):
        self.page = page
        self.screenshots_dir = screenshots_dir

        os.makedirs(
            self.screenshots_dir,
            exist_ok=True
        )

    async def open(self, url: str):
        await self.page.goto(
            url,
            wait_until="domcontentloaded"
        )

    async def inspect(self):

        locator = self.page.locator(
            self.SELECTOR
        )

        elements = await locator.evaluate_all(
            """
            elements => {

                function clean(value) {
                    return (value || "")
                        .replace(/\\\\s+/g, " ")
                        .trim();
                }

                function getLabel(element) {

                    const htmlId =
                        element.getAttribute("id");

                    if (htmlId) {

                        const explicitLabel =
                            document.querySelector(
                                `label[for="${CSS.escape(htmlId)}"]`
                            );

                        if (explicitLabel) {
                            return clean(
                                explicitLabel.innerText ||
                                explicitLabel.textContent
                            );
                        }
                    }

                    const parentLabel =
                        element.closest("label");

                    if (parentLabel) {
                        return clean(
                            parentLabel.innerText ||
                            parentLabel.textContent
                        );
                    }

                    return "";
                }

                function getContext(element) {

                    const fieldset =
                        element.closest("fieldset");

                    if (fieldset) {
                        return clean(
                            fieldset.innerText ||
                            fieldset.textContent
                        ).slice(0, 2000);
                    }

                    const radioGroup =
                        element.closest(
                            "[role='radiogroup']"
                        );

                    if (radioGroup) {
                        return clean(
                            radioGroup.innerText ||
                            radioGroup.textContent
                        ).slice(0, 2000);
                    }

                    let parent =
                        element.parentElement;

                    for (
                        let i = 0;
                        i < 4 && parent;
                        i++
                    ) {

                        const text = clean(
                            parent.innerText ||
                            parent.textContent
                        );

                        if (
                            text.length >= 20 &&
                            text.length <= 2000
                        ) {
                            return text;
                        }

                        parent = parent.parentElement;
                    }

                    return "";
                }

                return elements.map(
                    (element, index) => {

                        const label =
                            getLabel(element);

                        const context =
                            getContext(element);

                        const ownText =
                            clean(
                                element.innerText ||
                                element.textContent
                            );

                        const optionText =
                            label ||
                            ownText;

                        return {
                            id: index,

                            tag:
                                element.tagName
                                    ? element.tagName.toLowerCase()
                                    : "",

                            type:
                                element.getAttribute("type"),

                            name:
                                element.getAttribute("name"),

                            text:
                                optionText.slice(0, 1000),

                            placeholder:
                                element.getAttribute(
                                    "placeholder"
                                ),

                            value:
                                "value" in element
                                    ? element.value
                                    : element.getAttribute(
                                        "value"
                                    ),

                            checked:
                                "checked" in element
                                    ? Boolean(
                                        element.checked
                                    )
                                    : null,

                            aria_label:
                                element.getAttribute(
                                    "aria-label"
                                ),

                            html_id:
                                element.getAttribute(
                                    "id"
                                ),

                            context:
                                context
                        };
                    }
                );
            }
            """
        )

        body_text = await self.page.locator(
            "body"
        ).inner_text()

        return {
            "url": self.page.url,
            "title": await self.page.title(),
            "text": body_text[:50000],
            "elements": elements,
        }

    async def screenshot(
        self,
        step: int
    ):

        path = os.path.join(
            self.screenshots_dir,
            f"step_{step:03d}.png"
        )

        await self.page.screenshot(
            path=path,
            full_page=True
        )

        return path

    async def click(
        self,
        element_id: int
    ):

        element = self._element(
            element_id
        )

        await element.scroll_into_view_if_needed()

        await element.click()

    async def fill(
        self,
        element_id: int,
        value: str
    ):

        element = self._element(
            element_id
        )

        await element.scroll_into_view_if_needed()

        await element.fill(
            value
        )

    async def select(
        self,
        element_id: int,
        value: str | None = None
    ):

        element = self._element(
            element_id
        )

        tag = await element.evaluate(
            "element => element.tagName.toLowerCase()"
        )

        if tag == "select":

            if value is None:
                raise ValueError(
                    "select requires a value"
                )

            await element.select_option(
                value
            )

            return

        element_type = await element.get_attribute(
            "type"
        )

        if element_type in {
            "radio",
            "checkbox"
        }:

            await element.check()

            return

        await element.click()

    async def wait(
        self,
        milliseconds: int = 1000
    ):

        await self.page.wait_for_timeout(
            milliseconds
        )

    async def wait_for_test(
        self,
        timeout_seconds: int
    ):

        timeout_ms = (
            timeout_seconds * 1000
        )

        interval_ms = 500

        elapsed = 0

        while elapsed < timeout_ms:

            state = await self.inspect()

            elements = state[
                "elements"
            ]

            text = (
                state["text"] or ""
            ).strip()

            if elements:
                return state

            if len(text) > 150:
                return state

            await self.wait(
                interval_ms
            )

            elapsed += interval_ms

        return await self.inspect()

    def _element(
        self,
        element_id: int
    ):

        locator = self.page.locator(
            self.SELECTOR
        )

        return locator.nth(
            element_id
        )