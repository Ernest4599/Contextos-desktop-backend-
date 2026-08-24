"""
Gemini share-link parser using Google's internal 'batchexecute' RPC
protocol — confirmed working against a real, fresh Gemini share link.

Key fix from the first attempt: share.gemini.google/<short-id> is a
SHORT link that redirects to gemini.google.com/share/<real-id> — the
real ID must be resolved via that redirect first, not extracted
directly from the short URL.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List
from urllib.parse import quote

import requests

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def _resolve_share_id(url: str, timeout: int) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": BROWSER_USER_AGENT},
        allow_redirects=True,
        timeout=timeout,
    )
    match = re.search(r"/share/([a-f0-9]+)", response.url)
    if not match:
        raise ValueError(f"Could not resolve a Gemini share ID from: {url}")
    return match.group(1)


def _parse_batchexecute_response(raw_text: str) -> list:
    text = re.sub(r"^\)\]\}'\s*", "", raw_text)

    for line in text.split("\n"):
        line = line.strip()
        if not line or line.isdigit():
            continue
        try:
            frames = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(frames, list):
            continue
        for frame in frames:
            if not (isinstance(frame, list) and len(frame) >= 3 and frame[0] == "wrb.fr"):
                continue
            raw = frame[2]
            if not raw:
                continue
            return json.loads(raw) if isinstance(raw, str) else raw

    raise ValueError("Could not locate conversation data in Gemini response")


def parse_gemini_share(url: str, timeout: int = 20) -> List[Dict[str, str]]:
    share_id = _resolve_share_id(url, timeout)
    print(f"[GEMINI] Resolved share ID: {share_id}")

    inner = json.dumps([None, share_id, [4]], separators=(",", ":"))
    freq = json.dumps([[["ujx1Bf", inner, None, "generic"]]], separators=(",", ":"))
    body = f"f.req={quote(freq)}"

    response = requests.post(
        "https://gemini.google.com/_/BardChatUi/data/batchexecute?rpcids=ujx1Bf&rt=c",
        headers={
            "User-Agent": BROWSER_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
        },
        data=body,
        timeout=timeout,
    )
    response.raise_for_status()

    data = _parse_batchexecute_response(response.text)

    messages: List[Dict[str, str]] = []
    try:
        turns = data[0][1]
    except (IndexError, TypeError):
        turns = []

    for turn in turns or []:
        try:
            user_text = turn[2][0]
            if isinstance(user_text, list):
                user_text = user_text[0]
            if user_text:
                messages.append({"role": "user", "text": str(user_text).strip()})
        except (IndexError, TypeError):
            pass

        try:
            assistant_text = turn[3][0][0][1]
            if isinstance(assistant_text, list):
                assistant_text = assistant_text[0]
            if assistant_text:
                messages.append({"role": "assistant", "text": str(assistant_text).strip()})
        except (IndexError, TypeError):
            pass

    if not messages:
        raise ValueError("No messages found in Gemini share")

    return messages
