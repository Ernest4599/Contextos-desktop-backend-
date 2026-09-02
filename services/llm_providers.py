"""
Shared multi-provider LLM caller. Provider is selected via the
LLM_PROVIDER env var ("anthropic" | "openai" | "gemini"), each reading
its own API key from its usual env var. Used by both context_extractor
(share-link/paste/upload) and quick_prompt.

If the preferred provider fails (including transient errors like a
503 "model overloaded"), call_llm automatically falls through to any
other provider that has an API key configured, so a single provider's
outage doesn't take down the whole feature. The generic error is only
raised if every configured provider fails.

All failures are logged in detail server-side (print, visible in Render
logs) but surfaced to callers/users as one generic message - internal
config details (which env var is missing, which provider failed, raw
HTTP errors) should never reach the frontend.
"""
from __future__ import annotations

import os
import requests

GENERIC_ERROR_MESSAGE = "Something went wrong. Please try again."


class LLMProviderError(Exception):
    pass


def _call_anthropic(system_prompt: str, user_content: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[LLM] ANTHROPIC_API_KEY is not configured")
        raise LLMProviderError(GENERIC_ERROR_MESSAGE)

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-5",
                "max_tokens": 2000,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_content}],
            },
            timeout=60,
        )
        if not resp.ok:
            print(f"[LLM] Anthropic request failed: {resp.status_code} {resp.text}")
            raise LLMProviderError(GENERIC_ERROR_MESSAGE)
    except requests.RequestException as e:
        print(f"[LLM] Anthropic request failed: {e}")
        raise LLMProviderError(GENERIC_ERROR_MESSAGE)

    data = resp.json()
    parts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(parts)


def _call_openai(system_prompt: str, user_content: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[LLM] OPENAI_API_KEY is not configured")
        raise LLMProviderError(GENERIC_ERROR_MESSAGE)

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "max_tokens": 2000,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[LLM] OpenAI request failed: {e}")
        raise LLMProviderError(GENERIC_ERROR_MESSAGE)

    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_gemini(system_prompt: str, user_content: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[LLM] GEMINI_API_KEY is not configured")
        raise LLMProviderError(GENERIC_ERROR_MESSAGE)

    try:
        resp = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-flash-latest:generateContent?key={api_key}",
            json={
                "contents": [{"parts": [{"text": user_content}]}],
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "generationConfig": {"responseMimeType": "application/json"},
            },
            timeout=60,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[LLM] Gemini request failed: {e}")
        raise LLMProviderError(GENERIC_ERROR_MESSAGE)

    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


_PROVIDERS = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "gemini": _call_gemini,
}

_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

_PROVIDER_ORDER = ["anthropic", "openai", "gemini"]


def call_llm(system_prompt: str, user_content: str) -> str:
    preferred = os.environ.get("LLM_PROVIDER", "").lower()

    order: list[str] = []
    if preferred in _PROVIDERS:
        order.append(preferred)
    for name in _PROVIDER_ORDER:
        if name not in order:
            order.append(name)

    last_error: LLMProviderError | None = None
    tried_any = False

    for name in order:
        key_env = _PROVIDER_KEY_ENV[name]
        if not os.environ.get(key_env):
            continue  # skip providers with no key configured, no point trying

        tried_any = True
        try:
            return _PROVIDERS[name](system_prompt, user_content)
        except LLMProviderError as e:
            print(f"[LLM] Provider {name!r} failed, trying next available provider")
            last_error = e
            continue

    if not tried_any:
        print("[LLM] No LLM provider has an API key configured")

    raise last_error or LLMProviderError(GENERIC_ERROR_MESSAGE)


def parse_llm_json(raw: str) -> dict:
    import json
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[LLM] Failed to parse model output as JSON: {e}")
        raise LLMProviderError(GENERIC_ERROR_MESSAGE)
