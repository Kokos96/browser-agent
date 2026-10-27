import os

from playwright.async_api import Page
from playwright.async_api import Error as PlaywrightError


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
        # A navigation can destroy the DOM execution context.
        # Retry briefly instead of crashing the whole agent.
        last_error = None

        for attempt in range(4):
            try:
                return await self._inspect_once()
            except PlaywrightError as error:
                last_error = error
                if "Execution context was destroyed" not in str(error) and                    "most likely because of a navigation" not in str(error):
                    raise
                await self.page.wait_for_timeout(500)

        raise RuntimeError(
            f"Page kept navigating during inspection: {last_error}"
        )

    async def _inspect_once(self):

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

                // Tag the actual DOM nodes so later actions do not silently
                // retarget a different element if the page inserts/removes controls.
                const agentIdAttribute = "data-browser-agent-element-id";
                elements.forEach((element, index) => {
                    element.setAttribute(agentIdAttribute, String(index));
                });

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
        element = self._element(element_id)
        await element.scroll_into_view_if_needed()

        try:
            await element.click(timeout=5000)
            return
        except PlaywrightError as error:
            if "intercepts pointer events" not in str(error).lower():
                raise

        # A modal/share overlay can cover a valid target. Try the standard
        # non-destructive Escape dismissal before attempting any close control.
        await self._press_escape()
        element = self._element(element_id)
        await element.scroll_into_view_if_needed()
        try:
            await element.click(timeout=5000)
            return
        except PlaywrightError as error:
            if "intercepts pointer events" not in str(error).lower():
                raise

        # If Escape did not clear the blocker, try clearly labelled close/cancel
        # controls only. Never force-click through an overlay.
        await self._click_visible_close_control()
        element = self._element(element_id)
        await element.scroll_into_view_if_needed()
        await element.click(timeout=5000)

    async def fill(
        self,
        element_id: int,
        value: str
    ):
        element = self._element(element_id)
        await element.scroll_into_view_if_needed()

        try:
            await element.fill(value, timeout=5000)
            return
        except PlaywrightError as error:
            if "intercepts pointer events" not in str(error).lower():
                raise

        await self._press_escape()
        element = self._element(element_id)
        await element.scroll_into_view_if_needed()
        try:
            await element.fill(value, timeout=5000)
            return
        except PlaywrightError as error:
            if "intercepts pointer events" not in str(error).lower():
                raise

        await self._click_visible_close_control()
        element = self._element(element_id)
        await element.scroll_into_view_if_needed()
        await element.fill(value, timeout=5000)

    async def _press_escape(self):
        """Attempt the safest generic way to dismiss an obstructing overlay."""
        try:
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(150)
        except PlaywrightError:
            # Some embedded pages do not expose keyboard focus reliably.
            pass

    async def _click_visible_close_control(self) -> bool:
        """Click a visible, explicitly labelled close/cancel control, if present."""
        selectors = (
            '[role="dialog"] button[aria-label*="close" i]',
            '[aria-modal="true"] button[aria-label*="close" i]',
            '[class*="overlay" i] button[aria-label*="close" i]',
            'button[aria-label*="close" i]',
            'button[title*="close" i]',
            '[role="button"][aria-label*="close" i]',
            'button[aria-label*="dismiss" i]',
            'button[title*="dismiss" i]',
            'button[aria-label*="закрити" i]',
            'button[title*="закрити" i]',
            'button[aria-label*="закрыть" i]',
            'button[title*="закрыть" i]',
            'button[aria-label*="cancel" i]',
            'button[title*="cancel" i]',
            'button[aria-label*="скасувати" i]',
            'button[class*="close" i]',
        )

        for selector in selectors:
            try:
                matches = self.page.locator(selector)
                count = min(await matches.count(), 5)
            except PlaywrightError:
                continue

            for index in range(count):
                control = matches.nth(index)
                try:
                    if not await control.is_visible() or not await control.is_enabled():
                        continue
                    await control.click(timeout=1000)
                    await self.page.wait_for_timeout(150)
                    return True
                except PlaywrightError:
                    continue
        return False

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
        # IDs are written onto actual DOM nodes during inspect(). A unique
        # attribute locator avoids nth(index) silently selecting the wrong node
        # if a dynamic page changes its interactive-element order.
        return self.page.locator(
            f'[data-browser-agent-element-id="{element_id}"]'
        )