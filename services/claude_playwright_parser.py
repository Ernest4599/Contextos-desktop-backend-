"""
Claude share extractor via Playwright, network-intercept style.

Instead of guessing at DOM structure or JSON-embedding patterns, this
lets a real browser session navigate to the share page normally (which
gets past Cloudflare, per earlier confirmed testing), then intercepts
the browser's OWN internal request to /api/chat_snapshots/{uuid} - the
same endpoint that returned 403 when called standalone, but which the
page's own JS calls successfully once real session/cookie context
exists.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Response

try:
    from playwright_stealth import stealth_async
except ImportError:
    stealth_async = None

_SHARE_PATH_RE = re.compile(
    r"^/share/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$",
    re.IGNORECASE,
)


class ClaudeExtractError(Exception):
    pass


def _extract_uuid(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host not in ("claude.ai", "www.claude.ai"):
        raise ClaudeExtractError(f"Not a claude.ai URL: {url!r}")
    match = _SHARE_PATH_RE.match(parsed.path)
    if not match:
        raise ClaudeExtractError(f"Could not find a Claude share UUID in this URL: {url!r}")
    return match.group(1).lower()


def _message_text(msg: dict) -> str:
    if msg.get("text"):
        return str(msg["text"]).strip()
    content = msg.get("content")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]).strip())
        return "\n\n".join(p for p in parts if p)
    if isinstance(content, str):
        return content.strip()
    return ""


def _normalize_role(sender: Optional[str]) -> str:
    s = (sender or "").lower()
    return "user" if s in ("human", "user") else "assistant"


def _parse_snapshot_json(data: dict) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    for msg in data.get("chat_messages") or data.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        text = _message_text(msg)
        if not text:
            continue
        role = _normalize_role(msg.get("sender") or msg.get("role"))
        messages.append({"role": role, "text": text})
    return messages


async def extract_claude_share(url: str, timeout_ms: int = 30000) -> List[Dict[str, str]]:
    uuid = _extract_uuid(url)
    snapshot_holder: Dict[str, Any] = {}

    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = await context.new_page()

            if stealth_async:
                await stealth_async(page)
            else:
                await page.add_init_script(
                    """
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.chrome = { runtime: {} };
                    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                    """
                )

            async def on_response(resp: Response) -> None:
                try:
                    if "chat_snapshots" not in resp.url:
                        return
                    if resp.status != 200:
                        return
                    data = await resp.json()
                    if isinstance(data, dict) and ("chat_messages" in data or "messages" in data):
                        snapshot_holder["data"] = data
                except Exception:
                    pass

            page.on("response", on_response)

            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            for _ in range(20):
                if snapshot_holder.get("data"):
                    break
                await page.wait_for_timeout(1000)

            await browser.close()
            browser = None

            if snapshot_holder.get("data"):
                messages = _parse_snapshot_json(snapshot_holder["data"])
                if messages:
                    return messages
                raise ClaudeExtractError("Intercepted snapshot had no messages")

            raise ClaudeExtractError("Never intercepted a chat_snapshots response")

    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
