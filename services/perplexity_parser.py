""" 
Perplexity share-link parser.
curl_cffi + Chrome TLS impersonation → GET /rest/thread/{uuid}
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List
from urllib.parse import urlparse

from curl_cffi import requests as crequests

_UUID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


class PerplexityParseError(Exception):
    pass


def _extract_uuid(url: str) -> str:
    parsed = urlparse(url)
    if "perplexity.ai" not in (parsed.netloc or "").lower():
        raise PerplexityParseError(f"Not a perplexity.ai URL: {url!r}")
    match = _UUID_RE.search(parsed.path)
    if not match:
        raise PerplexityParseError(f"No Perplexity thread UUID in URL: {url!r}")
    return match.group(1)


def _extract_final_answer(step: dict) -> str:
    content = step.get("content") or {}
    answer = content.get("answer")

    if isinstance(answer, str):
        try:
            nested = json.loads(answer)
            if isinstance(nested, dict) and nested.get("answer"):
                return str(nested["answer"]).strip()
        except (json.JSONDecodeError, ValueError):
            pass
        return answer.strip()

    if isinstance(answer, dict) and answer.get("answer"):
        return str(answer["answer"]).strip()

    return ""


def parse_perplexity_share(url: str, timeout: int = 25) -> List[Dict[str, str]]:
    uuid = _extract_uuid(url)
    api_url = f"https://www.perplexity.ai/rest/thread/{uuid}"

    # "chrome" is the most portable target across curl_cffi builds
    response = crequests.get(
        api_url,
        impersonate="chrome",
        headers={
            "Accept": "application/json",
            "Referer": f"https://www.perplexity.ai/search/{uuid}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
        },
        timeout=timeout,
    )

    if response.status_code == 404:
        raise PerplexityParseError("This Perplexity share link was not found")
    if response.status_code == 403:
        raise PerplexityParseError(
            "Access denied (Cloudflare). Try impersonate='chrome' or a residential IP."
        )
    response.raise_for_status()

    data: Dict[str, Any] = response.json()
    entries = data.get("entries") or []
    messages: List[Dict[str, str]] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        query_text = (entry.get("query_str") or "").strip()
        answer_text = ""

        raw_text = entry.get("text")
        if raw_text:
            try:
                steps = json.loads(raw_text)
            except (json.JSONDecodeError, ValueError):
                steps = []
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    st = step.get("step_type")
                    if st == "INITIAL_QUERY" and not query_text:
                        q = (step.get("content") or {}).get("query")
                        if q:
                            query_text = str(q).strip()
                    elif st == "FINAL":
                        answer_text = _extract_final_answer(step)

        if query_text:
            messages.append({"role": "user", "content": query_text})
        if answer_text:
            messages.append({"role": "assistant", "content": answer_text})

    if not messages:
        raise PerplexityParseError("No messages found in Perplexity share")

    return messages
