"""
Shared multi-provider LLM caller. Provider is selected via the
LLM_PROVIDER env var ("anthropic" | "openai" | "gemini"), each reading
its own API key from its usual env var. Used by both context_extractor
(share-link/paste/upload) and quick_prompt.

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
                "model": "claude-sonnet-4-6",
                "max_tokens": 2000,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_content}],
            },
            timeout=60,
        )
        resp.raise_for_status()
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
            f"gemini-2.0-flash:generateContent?key={api_key}",
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


def call_llm(system_prompt: str, user_content: str) -> str:
    provider_name = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    provider_fn = _PROVIDERS.get(provider_name)
    if provider_fn is None:
        print(f"[LLM] Unknown LLM_PROVIDER: {provider_name!r}")
        raise LLMProviderError(GENERIC_ERROR_MESSAGE)
    return provider_fn(system_prompt, user_content)


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
