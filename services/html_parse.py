"""
Given HTML (from a plain fetch or a headless-browser render), try to
find the conversation content.

Two strategies, tried in order:
  1. Embedded JSON state (e.g. a <script> tag holding a framework's
     hydration data). Cheapest and most reliable when present.
  2. DOM elements carrying role-indicating attributes.
"""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Tuple

from bs4 import BeautifulSoup

from services.json_heuristics import best_message_list, normalize_role

_ASSIGNMENT_RE = re.compile(r"=\s*([\{\[].*)", re.DOTALL)


def _extract_first_balanced_json(text: str) -> Optional[Any]:
    start = None
    open_ch = None
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            open_ch = ch
            break
    if start is None:
        return None

    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_string = False
    string_quote = ""
    escape = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == string_quote:
                in_string = False
            continue
        if ch in ("'", '"'):
            in_string = True
            string_quote = ch
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    return None
    return None


def extract_json_blobs(html: str) -> List[Any]:
    soup = BeautifulSoup(html, "html.parser")
    blobs: List[Any] = []

    for script in soup.find_all("script"):
        raw = script.string
        if raw is None:
            raw = script.get_text()
        if not raw or not raw.strip():
            continue
        raw = raw.strip()

        script_type = (script.get("type") or "").lower()
        if "json" in script_type:
            try:
                blobs.append(json.loads(raw))
                continue
            except (json.JSONDecodeError, ValueError):
                pass

        match = _ASSIGNMENT_RE.search(raw)
        candidate_text = match.group(1) if match else raw

        parsed = _extract_first_balanced_json(candidate_text)
        if parsed is not None:
            blobs.append(parsed)

    return blobs


_ROLE_ATTR_RE = re.compile(r"(author[-_]?role|message[-_]?role|data[-_]?role|data[-_]?author)", re.IGNORECASE)


def extract_dom_role_messages(html: str) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results: List[Tuple[str, str]] = []

    for tag in soup.find_all(True):
        for attr_name, attr_val in tag.attrs.items():
            if not _ROLE_ATTR_RE.search(attr_name):
                continue
            val = attr_val if isinstance(attr_val, str) else " ".join(attr_val)
            role = normalize_role(val)
            if role is None:
                continue
            text = tag.get_text(separator=" ", strip=True)
            if text:
                results.append((role, text))
            break

    return results


def parse_html(html: str) -> Optional[Tuple[List[Tuple[str, str]], str]]:
    if not html:
        return None

    for blob in extract_json_blobs(html):
        messages = best_message_list(blob)
        if messages:
            return messages, "json_blob"

    dom_messages = extract_dom_role_messages(html)
    if len(dom_messages) >= 2:
        return dom_messages, "dom_attribute"

    return None
