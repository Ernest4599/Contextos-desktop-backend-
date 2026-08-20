import traceback
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async


class FetchError(Exception):
    """Raised when a share link's conversation cannot be fetched."""
    pass


async def fetch_conversation_html(url: str, timeout_ms: int = 30000) -> str:
    captured_requests = []

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

            def log_response(response):
                url_lower = response.url.lower()
                if "api" in url_lower or "chat_conversation" in url_lower or "share" in url_lower:
                    captured_requests.append(f"{response.status} {response.url}")

            page.on("response", log_response)

            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(8000)

            html = await page.content()
            await browser.close()

            print("=== CAPTURED NETWORK REQUESTS ===")
            for req in captured_requests:
                print(req)
            print("==================================")

            return html

    except Exception as e:
        print("=== FETCH ERROR ===")
        print(f"URL: {url}")
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {e}")
        traceback.print_exc()
        print("===================")
        raise FetchError(f"Could not read this link: {e}")
