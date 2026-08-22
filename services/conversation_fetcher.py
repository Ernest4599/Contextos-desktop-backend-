import traceback
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

from services.tiered_fetch import try_plain_fetch, MIN_REAL_CONTENT_CHARS


class FetchError(Exception):
    """Raised when a share link's conversation cannot be fetched by any tier."""
    pass


async def _render_with_browser(url: str, timeout_ms: int = 20000) -> str:
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()
            await stealth_async(page)

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
    """
    Tiered fetch: try cheap plain-HTTP methods first, only fall back
    to a real headless browser as a last resort.
    """
    print("[FETCH] Trying Tier 1/2: plain fetch + JSON hydration")
    text = try_plain_fetch(url)
    if text:
        print(f"[FETCH] Succeeded via plain fetch tier, {len(text)} characters")
        return text

    print("[FETCH] Cheap tiers failed, falling back to headless browser (last resort)")
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
