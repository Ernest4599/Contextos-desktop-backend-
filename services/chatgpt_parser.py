"""
ChatGPT share-link parser, ported from the proven open-source ChatPeek
project (github.com/vl3c/ChatPeek). Uses a plain HTTP request with
browser-matching headers, then extracts the conversation from React
Router's server-streamed loader data — NOT a <script type="application/
json"> block, but JS calls to streamController.enqueue(...) inside a
regular <script> tag. This is why our earlier JSON-scanning found
nothing: we were looking for the wrong shape entirely.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple, cast

import requests

DEFAULT_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
    ),
    "Sec-Ch-Ua": '"Chromium";v="118", "Not=A?Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class _ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_script = False
        self._current_attrs: Dict[str, str] = {}
        self._current_data: List[str] = []
        self.scripts: List[Tuple[Dict[str, str], str]] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag.lower() == "script":
            self._in_script = True
            self._current_attrs = {name: (value or "") for name, value in attrs}
            self._current_data = []

    def handle_endtag(self, tag) -> None:
        if tag.lower() == "script" and self._in_script:
            content = "".join(self._current_data)
            self.scripts.append((self._current_attrs, content))
            self._in_script = False
            self._current_attrs = {}
            self._current_data = []

    def handle_data(self, data) -> None:
        if self._in_script:
            self._current_data.append(data)


def _extract_scripts(html: str) -> List[Tuple[Dict[str, str], str]]:
    parser = _ScriptCollector()
    parser.feed(html)
    parser.close()
    return parser.scripts


class ShareAccessError(RuntimeError):
    pass


def fetch_share_page(url: str, timeout: int = 30) -> str:
    merged_headers = {**DEFAULT_HEADERS, "Referer": "https://chatgpt.com/"}
    response = requests.get(url, headers=merged_headers, timeout=timeout)
    response.raise_for_status()
    return response.text


def extract_loader_payload(html: str) -> Optional[List[Any]]:
    """Extract the React Router streamed loader payload if present."""
    for _attrs, text in _extract_scripts(html):
        if not text or "streamController.enqueue" not in text:
            continue

        decoder = json.JSONDecoder()
        start = 0
        while True:
            anchor = text.find("streamController.enqueue(", start)
            if anchor == -1:
                break
            anchor += len("streamController.enqueue(")
            quote_pos = text.find('"', anchor)
            next_close = text.find(");", anchor)

            if quote_pos != -1 and (next_close == -1 or quote_pos < next_close):
                try:
                    chunk, end_offset = decoder.raw_decode(text, quote_pos)
                except json.JSONDecodeError:
                    start = anchor + 1
                    continue
                start = end_offset
            else:
                end = text.find(");", anchor)
                if end == -1:
                    break
                chunk = text[anchor:end].strip()
                if chunk.startswith("(") and chunk.endswith(")"):
                    chunk = chunk[1:-1].strip()
                start = end + 2

            if isinstance(chunk, str):
                chunk = chunk.strip()
            if isinstance(chunk, str) and chunk.startswith("["):
                try:
                    parsed_chunk = json.loads(chunk)
                except json.JSONDecodeError:
                    parsed_chunk = None
                if isinstance(parsed_chunk, list):
                    return parsed_chunk
    return None


def decode_loader(loader: List[Any]) -> Dict[str, Any]:
    """Decode the flattened, reference-indexed loader list into normal dicts/lists."""
    cache: Dict[int, Any] = {}

    def decode_key(raw_key: Any) -> str:
        if isinstance(raw_key, str) and raw_key.startswith("_") and raw_key[1:].isdigit():
            idx = int(raw_key[1:])
            if 0 <= idx < len(loader):
                candidate = loader[idx]
                if isinstance(candidate, str):
                    return candidate
        return str(raw_key)

    def resolve(value: Any) -> Any:
        if type(value) is int:
            if value in cache:
                return cache[value]
            if not (0 <= value < len(loader)):
                return value
            cache[value] = None
            resolved_value = resolve(loader[value])
            cache[value] = resolved_value
            return resolved_value
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if isinstance(value, dict):
            return {decode_key(k): resolve(v) for k, v in value.items()}
        return value

    resolved: Dict[str, Any] = {}
    iterator = iter(loader[1:])
    for key in iterator:
        try:
            value = next(iterator)
        except StopIteration:
            break
        if isinstance(key, str) and key not in resolved:
            resolved[key] = resolve(value)
    return resolved


def _flatten_content(content: Dict[str, Any]) -> str:
    content_type = content.get("content_type")

    if content_type == "text":
        parts = content.get("parts", [])
        if isinstance(parts, list):
            return "\n\n".join(p for p in parts if isinstance(p, str)).strip()

    if content_type == "code":
        text = content.get("text", "")
        return text if isinstance(text, str) else ""

    if content_type == "multimodal_text":
        segments = []
        parts = content.get("parts", [])
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, str):
                    segments.append(part)
        return "\n\n".join(segments).strip()

    if "parts" in content:
        parts = content.get("parts", [])
        if isinstance(parts, list):
            return "\n\n".join(str(p) for p in parts if p).strip()

    return ""


def _parse_data(data: Dict[str, Any]) -> List[Dict[str, str]]:
    mapping = data.get("mapping", {})
    sequence_field = data.get("linear_conversation", [])
    sequence = [e for e in sequence_field if isinstance(e, dict)] if isinstance(sequence_field, list) else []

    messages: List[Dict[str, str]] = []
    for entry in sequence:
        node_id = entry.get("id")
        if not isinstance(node_id, str):
            continue
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        author_info = message.get("author") or {}
        role = author_info.get("role") if isinstance(author_info, dict) else None
        if role == "system":
            continue
        content = message.get("content") or {}
        if not isinstance(content, dict):
            continue
        text = _flatten_content(content)
        if not text:
            continue
        messages.append({"role": role or "assistant", "text": text})

    return messages


def parse_modern_share(html: str) -> List[Dict[str, str]]:
    loader = extract_loader_payload(html)
    if loader is None:
        raise ValueError("Modern share payload not found")
    decoded = decode_loader(loader)
    loader_data = decoded.get("loaderData", {})
    route = loader_data.get("routes/share.$shareId.($action)", {}) if isinstance(loader_data, dict) else {}
    server_response = route.get("serverResponse", {}) if isinstance(route, dict) else {}
    data = server_response.get("data", {}) if isinstance(server_response, dict) else {}
    if not data:
        raise ValueError("No conversation data in modern share payload")
    return _parse_data(data)


def parse_legacy_share(html: str) -> List[Dict[str, str]]:
    script_content = None
    for attrs, text in _extract_scripts(html):
        if attrs.get("id") == "__NEXT_DATA__":
            script_content = text
            break
    if not script_content:
        raise ValueError("Legacy share payload not found")
    payload = json.loads(script_content)
    props = payload.get("props", {})
    page_props = props.get("pageProps", {})
    server_response = page_props.get("serverResponse", {})
    data = server_response.get("data", {})
    if not data:
        raise ValueError("No conversation data in legacy share payload")
    return _parse_data(data)


def parse_chatgpt_share(url: str) -> List[Dict[str, str]]:
    html = fetch_share_page(url)
    try:
        return parse_modern_share(html)
    except (ValueError, KeyError):
        return parse_legacy_share(html)
