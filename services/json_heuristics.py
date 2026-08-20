"""
Platform-agnostic detector for "this looks like a chat message" inside
arbitrary JSON. Instead of hardcoding each platform's exact JSON key
names, we recognize the *shape* of a message (a role indicator + text
content) wherever it appears, then pick the largest coherent list of them.

Conservative by design: a candidate list is only accepted if most of its
items look like real messages, to avoid false positives on unrelated
arrays that happen to share a "role" key.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple


ROLE_ALIASES = {
    "user": "user",
    "human": "user",
    "you": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "model": "assistant",
    "bot": "assistant",
    "gpt": "assistant",
    "chatgpt": "assistant",
    "claude": "assistant",
    "gemini": "assistant",
    "system": "system",
}

ROLE_KEYS = ("role", "author", "sender", "from", "speaker")
CONTENT_KEYS = ("content", "text", "message", "body", "value")

MIN_CANDIDATE_LIST_LEN = 2
MIN_HIT_RATIO = 0.6  # fraction of items in a list that must look message-shaped


def normalize_role(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    return ROLE_ALIASES.get(raw.strip().lower())


def _extract_role(item: dict) -> Optional[str]:
    for key in ROLE_KEYS:
        if key not in item:
            continue
        val = item[key]
        role = normalize_role(val)
        if role:
            return role
        if isinstance(val, dict) and "role" in val:
            role = normalize_role(val["role"])
            if role:
                return role
    return None


def _extract_content(item: dict) -> Optional[str]:
    for key in CONTENT_KEYS:
        if key not in item:
            continue
        val = item[key]
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, list):
            parts: List[str] = []
            for sub in val:
                if isinstance(sub, str) and sub.strip():
                    parts.append(sub.strip())
                elif isinstance(sub, dict):
                    for sub_key in ("text", "value", "content"):
                        sub_val = sub.get(sub_key)
                        if isinstance(sub_val, str) and sub_val.strip():
                            parts.append(sub_val.strip())
                            break
            if parts:
                return "\n".join(parts)
        if isinstance(val, dict):
            nested = _extract_content({"content": val.get("parts")}) if "parts" in val else None
            if nested:
                return nested
    return None


def extract_role_and_content(item: Any) -> Optional[Tuple[str, str]]:
    if not isinstance(item, dict):
        return None
    role = _extract_role(item)
    if role is None:
        return None
    content = _extract_content(item)
    if content is None:
        return None
    return role, content


def find_message_lists(obj: Any) -> List[list]:
    candidates: List[list] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            hits = 0
            for item in node:
                if isinstance(item, dict) and extract_role_and_content(item):
                    hits += 1
            if len(node) >= MIN_CANDIDATE_LIST_LEN and node:
                ratio = hits / len(node)
                if hits >= MIN_CANDIDATE_LIST_LEN and ratio >= MIN_HIT_RATIO:
                    candidates.append(node)
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)

    walk(obj)
    return candidates


def best_message_list(obj: Any) -> Optional[List[Tuple[str, str]]]:
    candidates = find_message_lists(obj)
    if not candidates:
        return None

    best = max(candidates, key=len)
    result: List[Tuple[str, str]] = []
    for item in best:
        parsed = extract_role_and_content(item)
        if parsed:
            result.append(parsed)

    return result if len(result) >= MIN_CANDIDATE_LIST_LEN else None
