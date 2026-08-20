import traceback
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async


class FetchError(Exception):
    """Raised when a share link's conversation cannot be fetched."""
    pass


async def fetch_conversation_html(url: str, timeout_ms: int = 20000) -> str:
    """
    Renders the given share link URL in a headless browser (with stealth
    patches to avoid basic bot detection) and returns the fully rendered HTML.
    Raises FetchError if the page fails to load in time.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
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

            # Give Cloudflare's challenge (if any) time to resolve,
            # and let the page finish rendering the actual conversation.
            await page.wait_for_timeout(5000)

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
