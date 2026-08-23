"""
Claude share-link parser, based on a proven working implementation
(GET /api/chat_snapshots/{uuid}, confirmed against real Claude share
links). No browser needed — a plain authenticated-looking GET request
returns the full conversation as JSON directly.
"""
from __future__ import annotations

import re
from typing import Dict, List

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_UUID_RE = re.compile(r"claude\.ai/share/([a-f0-9-]{36})", re.IGNORECASE)


def _extract_uuid(url: str) -> str:
    match = _UUID_RE.search(url)
    if not match:
        raise ValueError("Could not find a Claude share UUID in this URL")
    return match.group(1)


def parse_claude_share(url: str, timeout: int = 20) -> List[Dict[str, str]]:
    uuid = _extract_uuid(url)
    api_url = f"https://claude.ai/api/chat_snapshots/{uuid}"

    response = requests.get(api_url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    messages: List[Dict[str, str]] = []
    for msg in data.get("chat_messages", []):
        sender = msg.get("sender")
        role = "user" if sender == "human" else "assistant"

        text = msg.get("text")
        if not text and isinstance(msg.get("content"), list) and msg["content"]:
            first_block = msg["content"][0]
            if isinstance(first_block, dict):
                text = first_block.get("text", "")

        if text and text.strip():
            messages.append({"role": role, "text": text.strip()})

    return messages
