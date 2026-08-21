import traceback
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async


class FetchError(Exception):
    """Raised when a share link's conversation cannot be fetched."""
    pass


async def fetch_conversation_text(url: str, timeout_ms: int = 20000) -> str:
    """
    Renders the page with a real headless browser and returns the visible
    rendered TEXT (document.body.innerText) — matching what a real user
    would actually see on screen.
    """
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

    except Exception as e:
        print("=== FETCH ERROR ===")
        print(f"URL: {url}")
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {e}")
        traceback.print_exc()
        print("===================")
        raise FetchError(f"Could not read this link: {e}")
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
