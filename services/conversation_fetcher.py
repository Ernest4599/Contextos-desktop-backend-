import traceback
from playwright.async_api import async_playwright

from services.tiered_fetch import try_plain_fetch, MIN_REAL_CONTENT_CHARS


class FetchError(Exception):
    """Raised when a share link's conversation cannot be fetched by any tier."""
    pass


async def _render_with_browser(url: str, timeout_ms: int = 25000) -> str:
    """
    Uses Firefox instead of Chromium — Firefox's automation protocol
    isn't CDP (Chrome DevTools Protocol), which is what our earlier
    Chromium attempts got detected through (uncaught JS errors like
    'utils is not defined' appeared specifically when using Chromium,
    consistent with CDP-based detection).
    """
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; rv:121.0) "
                    "Gecko/20100101 Firefox/121.0"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            text = await page.evaluate("() => document.body.innerText")

            await browser.close()
            browser = None

            return text.strip() if text else ""
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

    print("[FETCH] Cheap tiers failed, falling back to headless browser (Firefox)")
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
