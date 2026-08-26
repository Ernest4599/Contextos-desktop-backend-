"""
Meta AI share-link parser. Meta AI's page embeds the full conversation
as JSON inside Next.js self.__next_f.push(...) calls, keyed under a
GraphQL result "fetch__XABConversationSnapshot" -> messages.edges[].node.

User messages: __typename "UserMessage", text in userContent.
Assistant messages: content is often just a reference like "$69" -
pointing to a React "T"-type chunk marker (e.g. "69:T9b9,") that is
its OWN push call, immediately followed by a SEPARATE push call
containing the actual raw text (split out because it's too long to
inline in one chunk). We pair each marker with the very next chunk.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from curl_cffi import requests as crequests

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
_TEXT_MARKER_RE = re.compile(r'^([0-9a-f]+):T[0-9a-f]+,$')
_REF_RE = re.compile(r'^\$([0-9a-f]+)$')


def _build_chunk_index(decoded_chunks: List[str]) -> Dict[str, str]:
    """Maps chunk-id -> its text content, pairing each 'id:Thexlen,'
    marker chunk with the very next chunk in sequence (the real text)."""
    index: Dict[str, str] = {}
    i = 0
    while i < len(decoded_chunks):
        match = _TEXT_MARKER_RE.match(decoded_chunks[i])
        if match and i + 1 < len(decoded_chunks):
            chunk_id = match.group(1)
            index[chunk_id] = decoded_chunks[i + 1]
            i += 2
            continue
        i += 1
    return index


def _resolve(value: Any, chunk_index: Dict[str, str]) -> Optional[str]:
    if isinstance(value, str):
        ref_match = _REF_RE.match(value)
        if ref_match:
            return chunk_index.get(ref_match.group(1))
        return value
    return None


def _collect_text_from_sections(sections: List[dict], chunk_index: Dict[str, str]) -> List[str]:
    texts: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "text" in node:
                resolved = _resolve(node["text"], chunk_index)
                if resolved:
                    texts.append(resolved)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(sections)
    return texts


def parse_metaai_share(url: str, timeout: int = 25) -> List[Dict[str, str]]:
    response = crequests.get(url, impersonate="chrome124", headers=BROWSER_HEADERS, timeout=timeout)
    response.raise_for_status()
    html = response.text

    raw_matches = _PUSH_RE.findall(html)
    decoded_chunks = []
    for raw in raw_matches:
        try:
            decoded_chunks.append(raw.encode().decode("unicode_escape"))
        except UnicodeDecodeError:
            decoded_chunks.append("")

    chunk_index = _build_chunk_index(decoded_chunks)

    messages: List[Dict[str, str]] = []

    for decoded in decoded_chunks:
        if "fetch__XABConversationSnapshot" not in decoded:
            continue

        colon_idx = decoded.find(":")
        json_part = decoded[colon_idx + 1:] if colon_idx != -1 else decoded

        try:
            data = json.loads(json_part)
        except json.JSONDecodeError:
            continue

        try:
            edges = data["data"]["fetch__XABConversationSnapshot"]["messages"]["edges"]
        except (KeyError, TypeError):
            continue

        for edge in edges:
            node = edge.get("node", {})
            typename = node.get("__typename", "")

            if typename == "UserMessage":
                text = node.get("userContent") or node.get("content")
                if text:
                    messages.append({"role": "user", "text": text.strip()})
            else:
                content = node.get("content")
                resolved_text = _resolve(content, chunk_index) if isinstance(content, str) else None

                if resolved_text and resolved_text.strip():
                    messages.append({"role": "assistant", "text": resolved_text.strip()})
                elif "sections" in node:
                    parts = _collect_text_from_sections(node["sections"], chunk_index)
                    if parts:
                        messages.append({"role": "assistant", "text": "\n\n".join(parts).strip()})

        if messages:
            break

    if not messages:
        raise ValueError("No messages found in Meta AI share")

    return messages
