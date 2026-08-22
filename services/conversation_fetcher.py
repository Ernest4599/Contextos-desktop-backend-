import base64
import traceback
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

from services.tiered_fetch import try_plain_fetch, MIN_REAL_CONTENT_CHARS

_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = { runtime: {} };
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
  parameters.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission })
    : originalQuery(parameters)
);
"""


class FetchError(Exception):
    """Raised when a share link's conversation cannot be fetched by any tier."""
    pass


async def _render_with_browser(url: str, timeout_ms: int = 25000, capture_screenshot: bool = False):
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )

            await context.add_init_script(_STEALTH_INIT_SCRIPT)

            page = await context.new_page()
            await stealth_async(page)

            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            await page.wait_for_timeout(3000)

            text = await page.evaluate("() => document.body.innerText")

            screenshot_b64 = None
            if capture_screenshot:
                screenshot_bytes = await page.screenshot(full_page=False)
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            print(f"[FETCH] Console errors during render: {len(console_errors)}")
            for err in console_errors[:10]:
                print(f"[FETCH]   {err}")

            await browser.close()
            browser = None

            result_text = text.strip() if text else ""

            if capture_screenshot:
                return result_text, screenshot_b64
            return result_text
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


async def fetch_conversation_text(url: str) -> str:
    print("[FETCH] Trying Tier 1/2: plain fetch + shape-based JSON detection")
    text = try_plain_fetch(url)
    if text:
        print(f"[FETCH] Succeeded via plain fetch tier, {len(text)} characters")
        return text

    print("[FETCH] Cheap tiers failed, falling back to headless browser")
    try:
        rendered = await _render_with_browser(url)
        print(f"[FETCH] Headless render got {len(rendered)} characters (need {MIN_REAL_CONTENT_CHARS}+)")
        if rendered and len(rendered) >= MIN_REAL_CONTENT_CHARS:
            return rendered
    except Exception as e:
        print("=== FETCH ERROR (browser tier) ===")
        print(f"URL: {url}")
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {e}")
        traceback.print_exc()
        print("===================================")

    raise FetchError("Could not read this link")


async def fetch_screenshot_debug(url: str):
    """Debug helper: renders the page and returns (text, screenshot_base64)."""
    return await _render_with_browser(url, capture_screenshot=True)
