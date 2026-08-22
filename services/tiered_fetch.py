"""
Tiered share-link fetch strategy:

  1. Plain HTTP fetch + strip HTML tags with regex.
  2. If that's too short, scan embedded JSON <script> tags for
     message-SHAPED data (role + content pairs).
  3. Only if both fail: render with a real headless browser (handled
     separately in conversation_fetcher.py).
"""
from __future__ import annotations

import json
import re
from typing import Optional

import requests

from services.json_heuristics import best_message_list

MIN_REAL_CONTENT_CHARS = 150

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_NBSP_RE = re.compile(r"&nbsp;")
_WHITESPACE_RE = re.compile(r"\s{2,}")

_JSON_SCRIPT_RE = re.compile(
    r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def strip_html_tags(html: str) -> str:
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _COMMENT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = _NBSP_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def try_plain_fetch(url: str, timeout_s: int = 8) -> Optional[str]:
    try:
        response = requests.get(
            url,
            timeout=timeout_s,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
    except requests.RequestException as e:
        print(f"[FETCH] Plain fetch failed: {e}")
        return None

    print(f"[FETCH] Plain fetch HTTP status: {response.status_code}")
    print(f"[FETCH] Response headers: {dict(response.headers)}")
    print(f"[FETCH] Raw response length: {len(response.text)}")
    print(f"[FETCH] Raw response (first 1000 chars): {response.text[:1000]}")

    if response.status_code in (401, 403):
        print(f"[FETCH] Plain fetch: page requires login ({response.status_code})")
        return None
    if response.status_code == 404:
        print("[FETCH] Plain fetch: page not found (404)")
        return None
    if not response.ok:
        print(f"[FETCH] Plain fetch: status {response.status_code}")
        return None

    html = response.text[:2_000_000]

    stripped = strip_html_tags(html)
    print(f"[FETCH] Plain-fetch got {len(stripped)} stripped characters (need {MIN_REAL_CONTENT_CHARS}+)")

    if len(stripped) >= MIN_REAL_CONTENT_CHARS:
        return stripped

    json_blocks = _JSON_SCRIPT_RE.findall(html)
    print(f"[FETCH] Found {len(json_blocks)} JSON script blocks, trying shape-based detection")

    for block in json_blocks:
        try:
            parsed = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        messages = best_message_list(parsed)
        if messages:
            text = "\n\n".join(f"{role}: {content}" for role, content in messages)
            print(f"[FETCH] Shape-based detection found {len(messages)} messages, {len(text)} characters")
            if len(text) >= MIN_REAL_CONTENT_CHARS:
                return text

    print("[FETCH] No usable content found in plain fetch tiers")
    return None
