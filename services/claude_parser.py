"""
Claude share-link parser. Tries multiple strategies in order:
  1. Direct API (GET /api/chat_snapshots/{uuid}) - fast, clean JSON,
     but can return 403 if it requires session context we don't have.
  2. Fallback: parse the actual share PAGE, scanning for common
     framework data-embedding patterns:
       a) streamController.enqueue(...) - React Router / Remix style
       b) self.__next_f.push([...]) - Next.js App Router style
"""
from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict, Iterator, List
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_MIN_TEXT_LEN = 20
_MAX_DEPTH = 15
_MAX_COLLECTED_STRINGS = 20000

_SHARE_PATH_RE = re.compile(
    r"^/share/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$",
    re.IGNORECASE,
)


def _extract_uuid(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host not in ("claude.ai", "www.claude.ai"):
        raise ValueError(f"Not a claude.ai URL: {url!r}")
    match = _SHARE_PATH_RE.match(parsed.path)
    if not match:
        raise ValueError(f"Could not find a Claude share UUID in this URL: {url!r}")
    return match.group(1).lower()


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
            messages.append({"role": role, "text": text, "source": "api"})
    return messages


class _ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_script = False
        self._current_data: List[str] = []
        self.scripts: List[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "script":
            self._in_script = True
            self._current_data = []

    def handle_endtag(self, tag) -> None:
        if tag == "script" and self._in_script:
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
    if depth > _MAX_DEPTH or len(out) > _MAX_COLLECTED_STRINGS:
        return
    if isinstance(obj, str):
        if len(obj) > _MIN_TEXT_LEN and re.search(r"\s", obj):
            out.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            _find_text_strings(item, out, depth + 1)
    elif isinstance(obj, dict):
        for v in obj.values():
            _find_text_strings(v, out, depth + 1)


def _iter_enqueue_arrays(script_text: str) -> Iterator[list]:
    call_marker = "streamController.enqueue("
    decoder = json.JSONDecoder()
    idx = 0
    while True:
        call_start = script_text.find(call_marker, idx)
        if call_start == -1:
            return
        args_start = call_start + len(call_marker)
        next_call = script_text.find(call_marker, args_start)
        region_end = next_call if next_call != -1 else len(script_text)

        quote_pos = script_text.find('"', args_start, region_end)
        while quote_pos != -1:
            try:
                chunk, _end = decoder.raw_decode(script_text, quote_pos)
            except json.JSONDecodeError:
                quote_pos = script_text.find('"', quote_pos + 1, region_end)
                continue

            if isinstance(chunk, str):
                stripped = chunk.strip()
                if stripped.startswith("["):
                    try:
                        parsed = json.loads(stripped)
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, list):
                        yield parsed
                        break

            quote_pos = script_text.find('"', quote_pos + 1, region_end)

        idx = region_end


def _iter_next_f_push_arrays(script_text: str) -> Iterator[Any]:
    """Yield every payload found in self.__next_f.push([id, data])
    calls - Next.js App Router's streaming data format."""
    call_marker = "self.__next_f.push("
    decoder = json.JSONDecoder()
    idx = 0
    while True:
        call_start = script_text.find(call_marker, idx)
        if call_start == -1:
            return
        args_start = call_start + len(call_marker)
        try:
            parsed, end_offset = decoder.raw_decode(script_text, args_start)
        except json.JSONDecodeError:
            idx = args_start + 1
            continue
        idx = end_offset
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str):
                    stripped = item.strip()
                    # Next.js often prefixes the payload with a digit
                    # and colon before the actual JSON, e.g. "3:[...]"
                    colon_match = re.match(r"^\d+:(.*)$", stripped, re.DOTALL)
                    candidate = colon_match.group(1) if colon_match else stripped
                    if candidate.startswith("[") or candidate.startswith("{"):
                        try:
                            yield json.loads(candidate)
                        except json.JSONDecodeError:
                            continue
                elif isinstance(item, (list, dict)):
                    yield item


def _try_page_scan(uuid: str, timeout: int) -> List[Dict[str, str]]:
    canonical_url = f"https://claude.ai/share/{uuid}"
    response = requests.get(canonical_url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    html = response.text

    scripts = _extract_scripts(html)
    logger.info("page fetch: %d chars, %d <script> tags", len(html), len(scripts))

    has_stream_controller = any("streamController.enqueue" in s for s in scripts)
    has_next_f = any("self.__next_f.push" in s for s in scripts)
    logger.info(
        "pattern check: streamController.enqueue=%s, self.__next_f.push=%s",
        has_stream_controller, has_next_f,
    )

    all_strings: List[str] = []

    for script_text in scripts:
        if "streamController.enqueue" in script_text:
            for array_payload in _iter_enqueue_arrays(script_text):
                _find_text_strings(array_payload, all_strings)

    if not all_strings:
        for script_text in scripts:
            if "self.__next_f.push" in script_text:
                for payload in _iter_next_f_push_arrays(script_text):
                    _find_text_strings(payload, all_strings)

    deduped = list(dict.fromkeys(all_strings))
    if deduped:
        logger.info("page scan recovered %d candidate text strings", len(deduped))
    else:
        logger.warning("no known embedding pattern yielded usable text")

    return [{"role": "raw", "text": s, "source": "page_scan"} for s in deduped]


def parse_claude_share(url: str, timeout: int = 20) -> List[Dict[str, str]]:
    uuid = _extract_uuid(url)

    try:
        messages = _try_direct_api(uuid, timeout)
        if messages:
            logger.info("direct API succeeded: %d messages", len(messages))
            return messages
        logger.info("direct API returned no messages, falling back to page scan")
    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.warning("direct API failed (%s), falling back to page scan", e)

    return _try_page_scan(uuid, timeout)
