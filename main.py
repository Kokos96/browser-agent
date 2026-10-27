"""Worker entry point for Browser Automation Agent."""
import asyncio
import os
import sys
from pathlib import Path

# Support running directly from the repository checkout and in the Cloud Run image.
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from playwright.async_api import async_playwright

from browser_agent.agent import BrowserAgent
from browser_agent.browser import BrowserController
from browser_agent.config import settings
from browser_agent.llm import GeminiClient
from public_url_security import (
    PublicEgressProxy,
    UnsafePublicURL,
    host_allowed_by_policy,
    validate_public_web_url,
)


async def block_non_web_schemes(route):
    """Validate every browser request, including redirects and subresources."""
    try:
        await validate_public_web_url(route.request.url, require_https=False)
    except UnsafePublicURL as error:
        # A redirect or subresource must not bypass the initial URL policy.
        print(f"Blocked unsafe browser request: {route.request.url} ({error})")
        await route.abort("blockedbyclient")
        return
    await route.continue_()


async def main() -> bool:
    """Run one browser session with validated HTTPS and guarded browser egress."""
    try:
        target, _resolved_addresses = await validate_public_web_url(
            settings.test_url, require_https=True
        )
    except UnsafePublicURL as error:
        print(f"ERROR: target URL rejected by public HTTPS policy: {error}")
        return False

    access_mode = os.getenv("URL_POLICY_MODE", "public_https").strip().lower()
    allowed_hosts = {
        host.strip().lower()
        for host in os.getenv("ALLOWED_TEST_HOSTS", "").split(",")
        if host.strip()
    }
    if not host_allowed_by_policy(target.hostname, access_mode, allowed_hosts):
        print("ERROR: target URL rejected by the configured strict allowlist policy.")
        return False

    # Each worker owns its own loopback-only proxy so it cannot inherit
    # an arbitrary external proxy URL from the environment.
    owned_proxy = await PublicEgressProxy().start()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=settings.headless,
                proxy={"server": owned_proxy.proxy_url, "bypass": "<-loopback>"},
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
        await owned_proxy.close()


def exit_code_for_run_result(result: bool | None) -> int:
    """Map an explicit successful result to 0; failure or missing result to 1."""
    return 0 if result is True else 1


if __name__ == "__main__":
    raise SystemExit(exit_code_for_run_result(asyncio.run(main())))
