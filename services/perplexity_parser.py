"""
Perplexity share-link parser. Uses curl_cffi (TLS-fingerprint
impersonation of real Chrome) to call the direct REST API - confirmed
working against a real Perplexity share link. Plain requests gets 403
(Cloudflare); curl_cffi's TLS-level Chrome impersonation gets through.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List
from urllib.parse import urlparse

from curl_cffi import requests as crequests

_UUID_RE = re.compile(
    r"/search/(?:.*-)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


class PerplexityParseError(Exception):
    pass


def _extract_uuid(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if "perplexity.ai" not in host:
        raise PerplexityParseError(f"Not a perplexity.ai URL: {url!r}")
    match = _UUID_RE.search(parsed.path)
    if not match:
        raise PerplexityParseError(f"Could not find a Perplexity thread UUID in this URL: {url!r}")
    return match.group(1)


def _extract_final_answer(step: dict) -> str:
    content = step.get("content", {})
    answer = content.get("answer")

    if isinstance(answer, str):
        # Sometimes the answer field is itself a JSON-encoded string
        try:
            nested = json.loads(answer)
            if isinstance(nested, dict) and "answer" in nested:
                return str(nested["answer"]).strip()
        except (json.JSONDecodeError, ValueError):
            pass
        return answer.strip()

    if isinstance(answer, dict) and "answer" in answer:
        return str(answer["answer"]).strip()

    return ""


def parse_perplexity_share(url: str, timeout: int = 20) -> List[Dict[str, str]]:
    uuid = _extract_uuid(url)
    api_url = f"https://www.perplexity.ai/rest/thread/{uuid}"

    response = crequests.get(
        api_url,
        impersonate="chrome124",
        headers={
            "Accept": "application/json",
            "Referer": f"https://www.perplexity.ai/search/{uuid}",
        },
        timeout=timeout,
    )

    if response.status_code == 404:
        raise PerplexityParseError("This Perplexity share link was not found")
    if response.status_code == 403:
        raise PerplexityParseError("Access to this Perplexity share link was denied")
    response.raise_for_status()

    data: Dict[str, Any] = response.json()
    entries = data.get("entries", [])

    messages: List[Dict[str, str]] = []

    for entry in entries:
        raw_text = entry.get("text")
        if not raw_text:
            continue
        try:
            steps = json.loads(raw_text)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(steps, list):
            continue

        query_text = ""
        answer_text = ""

        for step in steps:
            if not isinstance(step, dict):
                continue
            step_type = step.get("step_type")

            if step_type == "INITIAL_QUERY":
                query = step.get("content", {}).get("query")
                if query:
                    query_text = str(query).strip()

            elif step_type == "FINAL":
                answer_text = _extract_final_answer(step)

        if query_text:
            messages.append({"role": "user", "text": query_text})
        if answer_text:
            messages.append({"role": "assistant", "text": answer_text})

    if not messages:
        raise PerplexityParseError("No messages found in Perplexity share")

    return messages
