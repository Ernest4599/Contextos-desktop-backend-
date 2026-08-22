"""
Tiered share-link fetch strategy, matching the approach proven to work
in ContextOS mobile's backend:

  1. Plain HTTP fetch + strip HTML tags with regex (cheap, fast, no
     browser needed).
  2. If that's too short, scan embedded JSON <script> tags for any
     readable strings.
  3. Only if both fail: render with a real headless browser (handled
     separately in conversation_fetcher.py).
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

import requests

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


def collect_readable_strings(value, out: List[str], depth: int = 0) -> None:
    if depth > 12 or len(out) > 500:
        return

    if isinstance(value, str):
        trimmed = value.strip()
        if (trimmed.startswith("{") and trimmed.endswith("}")) or (
            trimmed.startswith("[") and trimmed.endswith("]")
        ):
            try:
                nested = json.loads(trimmed)
                collect_readable_strings(nested, out, depth + 1)
                return
            except (json.JSONDecodeError, ValueError):
                pass
        if len(value) > 15 and re.search(r"\s", value):
            out.append(value)
    elif isinstance(value, list):
        for item in value:
            collect_readable_strings(item, out, depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            collect_readable_strings(item, out, depth + 1)


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
    extracted: List[str] = []
    for block in json_blocks:
        try:
            parsed = json.loads(block)
            collect_readable_strings(parsed, extracted)
        except (json.JSONDecodeError, ValueError):
            continue

    from_json = "\n".join(extracted).strip()
    print(
        f"[FETCH] JSON-hydration found {len(json_blocks)} JSON blocks, "
        f"{len(extracted)} readable strings, {len(from_json)} total characters"
    )

    if len(from_json) >= MIN_REAL_CONTENT_CHARS:
        return from_json

    return None
