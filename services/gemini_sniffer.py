"""
TEMPORARY debug tool: loads a real Gemini share page in a headless
browser and captures the exact outgoing request(s) it makes to
batchexecute - the real RPC ID, payload shape, and headers - so we
can fix our reverse-engineered parser to match reality instead of
guessing from a secondhand document.
"""
from __future__ import annotations

from typing import Any, Dict, List

from playwright.async_api import async_playwright


async def sniff_gemini_requests(url: str, timeout_ms: int = 20000) -> List[Dict[str, Any]]:
    captured: List[Dict[str, Any]] = []
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
                )
            )
            page = await context.new_page()

            async def on_request(request):
                if "batchexecute" in request.url:
                    captured.append({
                        "url": request.url,
                        "method": request.method,
                        "post_data": request.post_data,
                    })

            page.on("request", on_request)

            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            await page.wait_for_timeout(3000)

            await browser.close()
            browser = None

            return captured
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
