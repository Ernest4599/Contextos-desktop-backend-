"""
DeepSeek share-link parser. chat.deepseek.com sits behind an AWS WAF
JS challenge (not just IP reputation) - a plain request or curl_cffi
TLS impersonation gets a challenge page, not the real content. This
requires a real browser (Playwright + stealth) to execute the
challenge JS and clear it.

KNOWN LIMITATION (same as claude_parser.py): even after the challenge
clears, AWS WAF may still flag Render's datacenter IP as a bot signal.
This is deferred pending a residential proxy - same as Claude's 403.
Built now so it's ready to test the moment that's in place, and works
locally today from a residential/mobile IP.
"""
from __future__ import annotations

import base64
import re
from typing import Dict, List
from urllib.parse import urlparse

from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

from services.html_parse import parse_html

_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = { runtime: {} };
"""


class DeepSeekParseError(Exception):
    pass


def _validate_url(url: str) -> None:
    host = (urlparse(url).netloc or "").lower()
    if "deepseek.com" not in host:
        raise DeepSeekParseError(f"Not a chat.deepseek.com URL: {url!r}")


async def parse_deepseek_share(url: str, timeout_ms: int = 30000) -> List[Dict[str, str]]:
    _validate_url(url)

    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
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

            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

            # AWS WAF challenge sometimes needs a manual "Begin" click,
            # and always needs extra time beyond networkidle to resolve.
            try:
                begin_button = page.get_by_text("Begin", exact=False)
                if await begin_button.count() > 0:
                    await begin_button.first.click(timeout=3000)
            except Exception:
                pass

            await page.wait_for_timeout(8000)

            html = await page.content()
            await browser.close()
            browser = None

            if "Let's confirm you are human" in html or "awsWafCookieDomainList" in html:
                raise DeepSeekParseError(
                    "Blocked by DeepSeek's bot challenge (AWS WAF) - "
                    "likely IP-reputation related, same limitation as the Claude parser. "
                    "Works from residential IPs; needs a residential proxy from Render."
                )

            result = parse_html(html)
            if result is None:
                raise DeepSeekParseError("No messages found in DeepSeek share page")

            messages, strategy = result
            print(f"[DEEPSEEK] Found {len(messages)} messages using strategy: {strategy}")

            return [{"role": role, "text": text} for role, text in messages if text]
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
