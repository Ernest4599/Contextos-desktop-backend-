from playwright.async_api import async_playwright


class FetchError(Exception):
    """Raised when a share link's conversation cannot be fetched."""
    pass


async def fetch_conversation_html(url: str, timeout_ms: int = 20000) -> str:
    """
    Renders the given share link URL in a headless browser and returns
    the fully rendered HTML (after JavaScript has executed).
    Raises FetchError if the page fails to load in time.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )

            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

            # Give the page a moment to finish any late rendering
            await page.wait_for_timeout(1500)

            html = await page.content()
            await browser.close()

            return html

    except Exception as e:
        raise FetchError(f"Could not read this link: {e}")
