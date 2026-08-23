"""
Gemini share-link parser, based on a proven working implementation
using Google's internal 'batchexecute' RPC protocol (confirmed against
real Gemini share links). No authentication needed, no browser needed.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Dict, List

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
}

_SHARE_ID_RE = re.compile(r"gemini\.google\.com/share/([a-zA-Z0-9]+)")


def _extract_share_id(url: str) -> str:
    match = _SHARE_ID_RE.search(url)
    if not match:
        raise ValueError("Could not find a Gemini share ID in this URL")
    return match.group(1)


def _parse_batchexecute_response(raw_text: str) -> list:
    """
    Google's batchexecute format: a ')]}\'' safety prefix, then
    alternating lines of [length, json_array] pairs.
    """
    text = raw_text.strip()
    if text.startswith(")]}'"):
        text = text[4:].strip()

    lines = [line for line in text.split("\n") if line.strip()]

    for line in lines:
        stripped = line.strip()
        if stripped.isdigit():
            continue
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, list) and len(parsed) > 0:
            for entry in parsed:
                if isinstance(entry, list) and len(entry) >= 3 and entry[0] == "wrb.fr":
                    inner = entry[2]
                    if isinstance(inner, str):
                        try:
                            return json.loads(inner)
                        except (json.JSONDecodeError, ValueError):
                            continue
    raise ValueError("Could not locate conversation data in Gemini response")


def parse_gemini_share(url: str, timeout: int = 20) -> List[Dict[str, str]]:
    share_id = _extract_share_id(url)

    inner_payload = json.dumps([None, share_id, [4]])
    f_req = json.dumps([[["ujx1Bf", inner_payload, None, "generic"]]])

    body = urllib.parse.urlencode({"f.req": f_req})

    api_url = "https://gemini.google.com/_/BardChatUi/data/batchexecute"
    response = requests.post(
        api_url,
        params={"rpcids": "ujx1Bf", "rt": "c"},
        data=body,
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()

    data = _parse_batchexecute_response(response.text)

    messages: List[Dict[str, str]] = []
    try:
        turns = data[0][1]
    except (IndexError, TypeError, KeyError):
        turns = []

    for turn in turns:
        try:
            user_text = turn[2][0]
            if user_text:
                messages.append({"role": "user", "text": str(user_text).strip()})
        except (IndexError, TypeError):
            pass

        try:
            assistant_text = turn[3][0][0][1]
            if assistant_text:
                messages.append({"role": "assistant", "text": str(assistant_text).strip()})
        except (IndexError, TypeError):
            pass

    return messages
