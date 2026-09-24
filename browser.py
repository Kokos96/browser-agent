from playwright.async_api import Browser, BrowserContext, Page, Playwright

from config import settings


class BrowserClient:
    def __init__(self, playwright: Playwright):
        self.playwright = playwright
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def start(self) -> Page:
        self.browser = await self.playwright.chromium.launch(
            headless=settings.headless
        )
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        await self.page.goto(settings.target_url, wait_until="domcontentloaded")
        return self.page

    async def screenshot(self, name: str = "page.png") -> str:
        if self.page is None:
            raise RuntimeError("BrowserClient is not started")
        path = f"{settings.screenshots_dir}/{name}"
        await self.page.screenshot(path=path, full_page=True)
        return path

    async def close(self) -> None:
        if self.browser is not None:
            await self.browser.close()