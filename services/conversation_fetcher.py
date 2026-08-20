import traceback
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async


class FetchError(Exception):
    """Raised when a share link's conversation cannot be fetched."""
    pass


async def fetch_conversation_html(url: str, timeout_ms: int = 30000) -> str:
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

            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            # Try to explicitly wait for real conversation content to mount,
            # instead of guessing with a fixed delay. Try several known
            # selector patterns used by chat UIs; if none show up in time,
            # fall back to a generous flat wait as a last resort.
            selectors_to_try = [
                '[data-testid^="conversation-turn"]',
                '[data-message-author-role]',
                'article',
                '[data-testid="user-message"]',
                '[data-testid="assistant-message"]',
            ]

            found_selector = None
            for selector in selectors_to_try:
                try:
                    await page.wait_for_selector(selector, timeout=6000)
                    found_selector = selector
                    print(f"[FETCH] Found content via selector: {selector}")
                    break
                except Exception:
                    continue

            if not found_selector:
                print("[FETCH] No known message selector appeared, falling back to flat wait")
                await page.wait_for_timeout(8000)
            else:
                # Give a moment for any remaining messages to finish rendering
                await page.wait_for_timeout(2000)

            html = await page.content()
            await browser.close()

            return html

    except Exception as e:
        print("=== FETCH ERROR ===")
        print(f"URL: {url}")
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {e}")
        traceback.print_exc()
        print("===================")
        raise FetchError(f"Could not read this link: {e}")
