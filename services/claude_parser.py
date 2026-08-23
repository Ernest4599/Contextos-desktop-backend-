"""
Claude share-link parser. Tries two strategies:
  1. Direct API (GET /api/chat_snapshots/{uuid}) — fast, clean JSON,
     but can return 403 if it requires session context we don't have.
  2. Fallback: parse the actual share PAGE the same way ChatPeek does
     for ChatGPT — searching for streamController.enqueue(...) loader
     data, since Claude's app may use the same React Router framework.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_UUID_RE = re.compile(r"claude\.ai/share/([a-f0-9-]{36})", re.IGNORECASE)


def _extract_uuid(url: str) -> str:
    match = _UUID_RE.search(url)
    if not match:
        raise ValueError("Could not find a Claude share UUID in this URL")
    return match.group(1)


def _try_direct_api(uuid: str, timeout: int) -> List[Dict[str, str]]:
    api_url = f"https://claude.ai/api/chat_snapshots/{uuid}"
    headers = {**DEFAULT_HEADERS, "Accept": "application/json"}
    response = requests.get(api_url, headers=headers, timeout=timeout)
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


class _ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_script = False
        self._current_data: List[str] = []
        self.scripts: List[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag.lower() == "script":
            self._in_script = True
            self._current_data = []

    def handle_endtag(self, tag) -> None:
        if tag.lower() == "script" and self._in_script:
            self.scripts.append("".join(self._current_data))
            self._in_script = False

    def handle_data(self, data) -> None:
        if self._in_script:
            self._current_data.append(data)


def _extract_scripts(html: str) -> List[str]:
    parser = _ScriptCollector()
    parser.feed(html)
    parser.close()
    return parser.scripts


def _find_text_strings(obj: Any, out: List[str], depth: int = 0) -> None:
    if depth > 15 or len(out) > 5000:
        return
    if isinstance(obj, str):
        if len(obj) > 20 and re.search(r"\s", obj):
            out.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            _find_text_strings(item, out, depth + 1)
    elif isinstance(obj, dict):
        for v in obj.values():
            _find_text_strings(v, out, depth + 1)


def _try_page_scan(uuid: str, url: str, timeout: int) -> List[Dict[str, str]]:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    html = response.text

    scripts = _extract_scripts(html)
    print(f"[CLAUDE] Page fetch got {len(html)} chars, {len(scripts)} script tags")

    for script_text in scripts:
        if "streamController.enqueue" not in script_text:
            continue
        decoder = json.JSONDecoder()
        start = 0
        while True:
            anchor = script_text.find("streamController.enqueue(", start)
            if anchor == -1:
                break
            anchor += len("streamController.enqueue(")
            quote_pos = script_text.find('"', anchor)
            if quote_pos == -1:
                break
            try:
                chunk, end_offset = decoder.raw_decode(script_text, quote_pos)
            except json.JSONDecodeError:
                start = anchor + 1
                continue
            start = end_offset
            if isinstance(chunk, str) and chunk.strip().startswith("["):
                try:
                    parsed_chunk = json.loads(chunk.strip())
                except json.JSONDecodeError:
                    continue
                strings: List[str] = []
                _find_text_strings(parsed_chunk, strings)
                if strings:
                    print(f"[CLAUDE] streamController pattern found {len(strings)} text strings")
                    return [{"role": "raw", "text": s} for s in strings]

    print("[CLAUDE] No streamController.enqueue pattern found in page")
    return []


def parse_claude_share(url: str, timeout: int = 20) -> List[Dict[str, str]]:
    uuid = _extract_uuid(url)

    try:
        messages = _try_direct_api(uuid, timeout)
        if messages:
            print(f"[CLAUDE] Direct API succeeded, {len(messages)} messages")
            return messages
    except Exception as e:
        print(f"[CLAUDE] Direct API failed: {e}")

    return _try_page_scan(uuid, url, timeout)
