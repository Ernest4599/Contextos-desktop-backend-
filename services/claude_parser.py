"""
Claude share-link parser. Uses curl_cffi (TLS-fingerprint impersonation
of real Chrome) to call the direct API — confirmed working against a
real, fresh Claude share link. A plain `requests` call to this same
endpoint returns 403; curl_cffi's TLS-level Chrome impersonation gets
past whatever was inspecting the connection at that layer.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List
from urllib.parse import urlparse

from curl_cffi import requests as crequests

logger = logging.getLogger(__name__)

_SHARE_PATH_RE = re.compile(
    r"^/share/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$",
    re.IGNORECASE,
)


class ClaudeParseError(Exception):
    pass


def _extract_uuid(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host not in ("claude.ai", "www.claude.ai"):
        raise ClaudeParseError(f"Not a claude.ai URL: {url!r}")
    match = _SHARE_PATH_RE.match(parsed.path)
    if not match:
        raise ClaudeParseError(f"Could not find a Claude share UUID in this URL: {url!r}")
    return match.group(1).lower()


def parse_claude_share(url: str, timeout: int = 20) -> List[Dict[str, str]]:
    uuid = _extract_uuid(url)
    api_url = f"https://claude.ai/api/chat_snapshots/{uuid}"

    response = crequests.get(
        api_url,
        impersonate="chrome124",
        headers={"Accept": "application/json"},
        timeout=timeout,
    )

    if response.status_code == 404:
        raise ClaudeParseError("This Claude share link was not found (it may have expired)")
    if response.status_code == 403:
        raise ClaudeParseError("Access to this Claude share link was denied")
    response.raise_for_status()

    data = response.json()

    messages: List[Dict[str, str]] = []
    for msg in data.get("chat_messages", []):
        sender = msg.get("sender")
        role = "user" if sender == "human" else "assistant"

        text = (msg.get("text") or "").strip()
        if not text:
            parts = []
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    block_text = (block.get("text") or "").strip()
                    if block_text:
                        parts.append(block_text)
            text = "\n\n".join(parts)

        if text:
            messages.append({"role": role, "text": text})

    if not messages:
        raise ClaudeParseError("No messages found in Claude share snapshot")

    logger.info("claude parser succeeded via curl_cffi: %d messages", len(messages))
    return messages
