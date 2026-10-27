"""Worker entry point for Browser Automation Agent."""
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

# Support running directly from the repository checkout and in the Cloud Run image.
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from playwright.async_api import async_playwright

from browser_agent.agent import BrowserAgent
from browser_agent.browser import BrowserController
from browser_agent.config import settings
from browser_agent.llm import GeminiClient
from public_url_security import PublicEgressProxy, UnsafePublicURL, validate_public_web_url


async def block_non_web_schemes(route):
    """Block file:, javascript:, and other non-HTTP(S) network navigations."""
    scheme = urlsplit(route.request.url).scheme.lower()
    if scheme not in {"http", "https"}:
        await route.abort("blockedbyclient")
        return
    await route.continue_()


async def main() -> bool:
    """Run one browser session with validated HTTPS and guarded browser egress."""
    try:
        await validate_public_web_url(settings.test_url, require_https=True)
    except UnsafePublicURL as error:
        print(f"ERROR: target URL rejected by public HTTPS policy: {error}")
        return False

    # The API process starts a shared container-local proxy and passes its URL
    # to this worker. Direct local runs create and own their own proxy instead.
    proxy_url = os.getenv("BROWSER_HTTP_PROXY")
    owned_proxy = None
    if not proxy_url:
        owned_proxy = await PublicEgressProxy().start()
        proxy_url = owned_proxy.proxy_url

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=settings.headless,
                proxy={"server": proxy_url, "bypass": "<-loopback>"},
                args=[
                    "--proxy-bypass-list=<-loopback>",
                    "--disable-quic",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                ],
            )
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 900})
                await page.route("**/*", block_non_web_schemes)
                browser_controller = BrowserController(page)
                agent = BrowserAgent(browser_controller, GeminiClient())
                return await agent.run()
            finally:
                await browser.close()
    finally:
        if owned_proxy is not None:
            await owned_proxy.close()


def exit_code_for_run_result(result: bool | None) -> int:
    """Map an explicit successful result to 0; failure or missing result to 1."""
    return 0 if result is True else 1


if __name__ == "__main__":
    raise SystemExit(exit_code_for_run_result(asyncio.run(main())))
