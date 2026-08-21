import traceback
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async


class FetchError(Exception):
    """Raised when a share link's conversation cannot be fetched."""
    pass


async def fetch_conversation_html(url: str, timeout_ms: int = 30000) -> str:
    console_errors = []
    failed_requests = []
    page_errors = []

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

            def on_console(msg):
                if msg.type == "error":
                    console_errors.append(msg.text)

            def on_page_error(exc):
                page_errors.append(str(exc))

            def on_request_failed(request):
                failed_requests.append(f"{request.method} {request.url} - {request.failure}")

            page.on("console", on_console)
            page.on("pageerror", on_page_error)
            page.on("requestfailed", on_request_failed)

            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

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
                await page.wait_for_timeout(2000)

            html = await page.content()
            await browser.close()

            print("=== BROWSER CONSOLE ERRORS ===")
            print(f"Count: {len(console_errors)}")
            for err in console_errors[:20]:
                print(err)
            print("=== PAGE ERRORS (uncaught exceptions) ===")
            print(f"Count: {len(page_errors)}")
            for err in page_errors[:20]:
                print(err)
            print("=== FAILED NETWORK REQUESTS ===")
            print(f"Count: {len(failed_requests)}")
            for req in failed_requests[:20]:
                print(req)
            print("===============================")

            return html

    except Exception as e:
        print("=== FETCH ERROR ===")
        print(f"URL: {url}")
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {e}")
        traceback.print_exc()
        print("===================")
        raise FetchError(f"Could not read this link: {e}")
