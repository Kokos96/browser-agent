import hashlib
import os
from typing import Any, Dict, Optional

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page


class BrowserController:
    """Async Playwright controller with rich DOM context extraction, overlay recovery, and selection verification."""

    SELECTOR = (
        "input, button, textarea, select, a, "
        "[role='button'], [role='radio'], [role='checkbox'], "
        "[role='option'], [data-option], [data-answer]"
    )

    def __init__(
        self,
        page: Page,
        screenshots_dir: str = "screenshots",
    ):
        self.page = page
        self.screenshots_dir = screenshots_dir
        os.makedirs(self.screenshots_dir, exist_ok=True)

    async def open(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded")

    async def inspect(self) -> Dict[str, Any]:
        last_error = None
        for _ in range(4):
            try:
                return await self._inspect_once()
            except PlaywrightError as error:
                last_error = error
                err_str = str(error)
                if (
                    "Execution context was destroyed" not in err_str
                    and "most likely because of a navigation" not in err_str
                ):
                    raise
                await self.page.wait_for_timeout(500)

        raise RuntimeError(f"Page kept navigating during inspection: {last_error}")

    async def _inspect_once(self) -> Dict[str, Any]:
        locator = self.page.locator(self.SELECTOR)
        elements = await locator.evaluate_all(
            r"""
            elements => {
                function clean(value) {
                    return (value || "").replace(/\s+/g, " ").trim();
                }

                function isVisible(el) {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (style.display === "none" || style.visibility === "hidden") return false;
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) {
                        const tag = (el.tagName || "").toLowerCase();
                        const type = (el.getAttribute("type") || "").toLowerCase();
                        if (tag === "input" && (type === "radio" || type === "checkbox")) {
                            const lbl = el.closest("label") || (el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null);
                            if (lbl) {
                                const lRect = lbl.getBoundingClientRect();
                                return lRect.width > 0 && lRect.height > 0;
                            }
                        }
                        return false;
                    }
                    return true;
                }

                function isInNavOrFooter(el) {
                    return Boolean(el.closest("nav, header, footer, [role='navigation'], [role='banner'], [role='contentinfo']"));
                }

                function getLabel(element) {
                    const htmlId = element.getAttribute("id");
                    if (htmlId) {
                        try {
                            const explicitLabel = document.querySelector(`label[for="${CSS.escape(htmlId)}"]`);
                            if (explicitLabel) {
                                return clean(explicitLabel.innerText || explicitLabel.textContent);
                            }
                        } catch (e) {}
                    }
                    const parentLabel = element.closest("label");
                    if (parentLabel) {
                        return clean(parentLabel.innerText || parentLabel.textContent);
                    }
                    return "";
                }

                function getQuestionInfo(element, containerCounter) {
                    const containerSelectors = [
                        "fieldset",
                        "[role='radiogroup']",
                        "[role='group']",
                        "[data-question]",
                        "[data-question-id]",
                        ".question",
                        ".quiz-question",
                        ".test-question",
                        "[class*='question']"
                    ];
                    let container = null;
                    for (const sel of containerSelectors) {
                        const candidate = element.closest(sel);
                        if (candidate && clean(candidate.innerText || candidate.textContent).length <= 2500) {
                            container = candidate;
                            break;
                        }
                    }

                    if (!container) {
                        let parent = element.parentElement;
                        for (let i = 0; i < 5 && parent && parent.tagName !== "BODY" && parent.tagName !== "MAIN" && parent.tagName !== "FORM"; i++) {
                            const inputs = parent.querySelectorAll("input[type='radio'], input[type='checkbox'], [role='radio'], [role='checkbox'], [role='option'], button");
                            const text = clean(parent.innerText || parent.textContent);
                            if (inputs.length >= 2 && text.length >= 15 && text.length <= 2000) {
                                container = parent;
                                break;
                            }
                            parent = parent.parentElement;
                        }
                    }

                    if (!container) {
                        let parent = element.parentElement;
                        for (let i = 0; i < 4 && parent; i++) {
                            const text = clean(parent.innerText || parent.textContent);
                            if (text.length >= 20 && text.length <= 2000) {
                                return {
                                    context: text.slice(0, 1500),
                                    heading: "",
                                    containerId: ""
                                };
                            }
                            parent = parent.parentElement;
                        }
                        return { context: "", heading: "", containerId: "" };
                    }

                    if (!container.getAttribute("data-agent-container-id")) {
                        containerCounter.val += 1;
                        container.setAttribute("data-agent-container-id", "q_cont_" + containerCounter.val);
                    }
                    const containerId = container.getAttribute("data-agent-container-id") || "";

                    let heading = "";
                    const headingEl = container.querySelector("legend, h1, h2, h3, h4, h5, p, .question-title, .question-text, [class*='question-title'], [class*='question-text'], strong");
                    if (headingEl && !headingEl.contains(element)) {
                        heading = clean(headingEl.innerText || headingEl.textContent);
                    }
                    const fullContext = clean(container.innerText || container.textContent).slice(0, 1500);
                    if (!heading) {
                        const firstLine = clean((container.innerText || "").split("\n")[0] || "");
                        if (firstLine.length >= 5 && firstLine.length <= 400) {
                            heading = firstLine;
                        }
                    }

                    return {
                        context: fullContext,
                        heading: heading.slice(0, 500),
                        containerId: containerId
                    };
                }

                const agentIdAttribute = "data-browser-agent-element-id";
                const containerCounter = { val: 0 };
                elements.forEach((element, index) => {
                    element.setAttribute(agentIdAttribute, String(index));
                });

                return elements.map((element, index) => {
                    const tag = element.tagName ? element.tagName.toLowerCase() : "";
                    const label = getLabel(element);
                    const qInfo = getQuestionInfo(element, containerCounter);
                    const ownText = clean(element.innerText || element.textContent);
                    const optionText = label || ownText;

                    let selectOptions = [];
                    if (tag === "select" && element.options) {
                        selectOptions = Array.from(element.options)
                            .filter(opt => !opt.disabled && clean(opt.text).length > 0)
                            .map(opt => ({
                                value: String(opt.value || ""),
                                text: clean(opt.text)
                            }));
                    }

                    const ariaChecked = element.getAttribute("aria-checked");
                    const ariaSelected = element.getAttribute("aria-selected");
                    const isChecked = ("checked" in element && element.checked !== undefined && (element.type === "radio" || element.type === "checkbox"))
                        ? Boolean(element.checked)
                        : (ariaChecked === "true" ? true : (ariaChecked === "false" ? false : null));

                    const isSelected = (ariaSelected === "true") || element.classList.contains("selected") || element.classList.contains("active");

                    return {
                        id: index,
                        tag: tag,
                        role: element.getAttribute("role"),
                        type: element.getAttribute("type"),
                        name: element.getAttribute("name"),
                        text: optionText.slice(0, 1000),
                        placeholder: element.getAttribute("placeholder"),
                        value: "value" in element && element.value !== undefined
                            ? String(element.value)
                            : element.getAttribute("value"),
                        checked: isChecked,
                        selected: Boolean(isSelected),
                        disabled: Boolean(element.disabled || element.getAttribute("aria-disabled") === "true"),
                        visible: isVisible(element),
                        in_nav_or_footer: isInNavOrFooter(element),
                        aria_label: element.getAttribute("aria-label"),
                        title: element.getAttribute("title"),
                        html_id: element.getAttribute("id"),
                        href: element.getAttribute("href"),
                        context: qInfo.context,
                        question_heading: qInfo.heading,
                        container_id: qInfo.containerId,
                        select_options: selectOptions
                    };
                });
            }
            """
        )

        try:
            body_text = await self.page.locator("body").inner_text()
        except PlaywrightError:
            body_text = ""

        url = self.page.url
        title = await self.page.title()
        sig_Source = f"{url}|{title}|{len(elements)}|{body_text[:1000]}"
        fingerprint = hashlib.sha1(sig_Source.encode("utf-8", errors="ignore")).hexdigest()[:16]

        return {
            "url": url,
            "title": title,
            "text": body_text[:50000],
            "elements": elements,
            "dom_fingerprint": fingerprint,
        }

    async def screenshot(self, step: int) -> str:
        path = os.path.join(self.screenshots_dir, f"step_{step:03d}.png")
        await self.page.screenshot(path=path, full_page=True)
        return path

    async def click(self, element_id: int) -> None:
        element = self._element(element_id)
        await element.scroll_into_view_if_needed()

        try:
            await element.click(timeout=5000)
            return
        except PlaywrightError as error:
            if "intercepts pointer events" not in str(error).lower():
                raise

        await self._press_escape()
        element = self._element(element_id)
        await element.scroll_into_view_if_needed()
        try:
            await element.click(timeout=5000)
            return
        except PlaywrightError as error:
            if "intercepts pointer events" not in str(error).lower():
                raise

        await self._click_visible_close_control()
        element = self._element(element_id)
        await element.scroll_into_view_if_needed()
        await element.click(timeout=5000)

    async def fill(self, element_id: int, value: str) -> None:
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

    async def _press_escape(self) -> None:
        try:
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(150)
        except PlaywrightError:
            pass

    async def _click_visible_close_control(self) -> bool:
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

    async def select(self, element_id: int, value: Optional[str] = None) -> None:
        element = self._element(element_id)
        tag = await element.evaluate("element => element.tagName.toLowerCase()")

        if tag == "select":
            if value is None:
                raise ValueError("select requires a value")
            try:
                await element.select_option(value=value, timeout=4000)
            except PlaywrightError:
                await element.select_option(label=value, timeout=4000)
            return

        element_type = (await element.get_attribute("type") or "").lower()
        if element_type in {"radio", "checkbox"}:
            try:
                await element.check(timeout=4000)
                return
            except PlaywrightError as err:
                err_lower = str(err).lower()
                if "intercepts pointer events" in err_lower:
                    await self._press_escape()
                    await self._click_visible_close_control()
                    try:
                        await element.check(timeout=3000)
                        return
                    except PlaywrightError:
                        pass
                # Fallback: click associated <label> when input is visually hidden/custom-styled
                clicked_label = await element.evaluate(
                    """el => {
                        const id = el.getAttribute('id');
                        if (id) {
                            const lbl = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                            if (lbl) { lbl.click(); return true; }
                        }
                        const parentLbl = el.closest('label');
                        if (parentLbl) { parentLbl.click(); return true; }
                        return false;
                    }"""
                )
                if clicked_label:
                    return
                raise

        await self.click(element_id)

    async def verify_selection(self, element_id: int, expected_value: Optional[str] = None) -> bool:
        try:
            element = self._element(element_id)
            if not hasattr(element, "evaluate"):
                return True
            result = await element.evaluate(
                """(el, expectedVal) => {
                    if (!el) return false;
                    const tag = (el.tagName || '').toLowerCase();
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    if (tag === 'input' && (type === 'radio' || type === 'checkbox')) {
                        return Boolean(el.checked);
                    }
                    if (tag === 'select') {
                        return expectedVal ? el.value === expectedVal : Boolean(el.value);
                    }
                    const ariaChecked = el.getAttribute('aria-checked');
                    const ariaSelected = el.getAttribute('aria-selected');
                    const ariaPressed = el.getAttribute('aria-pressed');
                    if (ariaChecked === 'true' || ariaSelected === 'true' || ariaPressed === 'true') {
                        return true;
                    }
                    const cls = (el.className || '') + ' ' + ((el.parentElement && el.parentElement.className) || '');
                    if (/selected|active|checked|chosen|current/i.test(cls)) {
                        return true;
                    }
                    return true;
                }""",
                expected_value,
            )
            return bool(result)
        except Exception:
            return True

    async def wait(self, milliseconds: int = 1000) -> None:
        await self.page.wait_for_timeout(milliseconds)

    async def wait_for_test(self, timeout_seconds: int) -> Dict[str, Any]:
        timeout_ms = timeout_seconds * 1000
        interval_ms = 500
        elapsed = 0

        while elapsed < timeout_ms:
            state = await self.inspect()
            elements = state["elements"]
            text = (state["text"] or "").strip()

            if elements or len(text) > 150:
                return state

            await self.wait(interval_ms)
            elapsed += interval_ms

        return await self.inspect()

    async def wait_for_transition(
        self,
        previous_fingerprint: str = "",
        timeout_ms: int = 2000,
    ) -> Dict[str, Any]:
        interval_ms = 250
        elapsed = 0
        while elapsed < timeout_ms:
            await self.wait(interval_ms)
            elapsed += interval_ms
            state = await self.inspect()
            if previous_fingerprint and state.get("dom_fingerprint") != previous_fingerprint:
                return state
        return await self.inspect()

    def _element(self, element_id: int):
        return self.page.locator(f'[data-browser-agent-element-id="{element_id}"]')
