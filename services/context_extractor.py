"""
LLM-based context extraction: takes a list of {role, text} messages and
produces a structured "Context Package" covering goals, current state,
decisions, completed work, tasks, constraints, open questions, key facts,
and the single next action - then fills the continuation template.

Provider is selected via the LLM_PROVIDER env var ("anthropic" | "openai"
| "gemini"), each reading its own API key from its usual env var. Swapping
providers on Render is an env var change, no code change. Until a key is
set, calls raise ExtractionError with a clear "not configured yet" message
instead of crashing.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import requests


class ExtractionError(Exception):
    pass


SECTIONS = [
    "goals", "current_state", "decisions", "completed_work", "tasks",
    "constraints", "open_questions", "key_facts",
]

EXTRACTION_SYSTEM_PROMPT = """You are analyzing a conversation between a user and an AI assistant to build a "Context Package" that lets the user resume their work in a fresh conversation.

Read the full conversation and extract the following, each as a list of short, plain-language sentences (empty list if none apply):

- goals: what the user is trying to achieve (e.g. "the goal is...", "trying to build...")
- current_state: how things stand right now (e.g. "the server is down", "currently at step 3")
- decisions: choices that were made and settled (e.g. "decided to use Postgres")
- completed_work: things already finished (e.g. "installed the sidebar", "wrote the script")
- tasks: things still left to do, no particular urgency (e.g. "need to fix the CSS")
- constraints: limits, restrictions, or deadlines (e.g. "only 7 days left", "must be mobile-only")
- open_questions: unresolved questions still being figured out
- key_facts: concrete details worth remembering - names, numbers, tools, paths (never secret values)

Also extract:
- next_action: the SINGLE most urgent next step, flagged by urgency language ("now", "next", "first", "immediately"). null if none is clearly flagged.

Respond with ONLY a JSON object with these exact keys: goals, current_state, decisions, completed_work, tasks, constraints, open_questions, key_facts, next_action. No preamble, no markdown fences, no extra commentary."""


def _messages_to_transcript(messages: List[Dict[str, str]]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "unknown")
        text = m.get("text", "")
        if text:
            lines.append(f"{role.upper()}: {text}")
    return "\n\n".join(lines)


def _call_anthropic(transcript: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExtractionError("ANTHROPIC_API_KEY is not configured yet")

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
            "system": EXTRACTION_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": transcript}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(parts)


def _call_openai(transcript: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ExtractionError("OPENAI_API_KEY is not configured yet")

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
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_gemini(transcript: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ExtractionError("GEMINI_API_KEY is not configured yet")

    resp = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={api_key}",
        json={
            "contents": [{"parts": [{"text": transcript}]}],
            "systemInstruction": {"parts": [{"text": EXTRACTION_SYSTEM_PROMPT}]},
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


_PROVIDERS = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "gemini": _call_gemini,
}


def _parse_llm_json(raw: str) -> Dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as e:
        raise ExtractionError(f"Model returned non-JSON output: {e}")


def extract_context(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Runs the configured LLM provider once over the full conversation and
    returns a dict with all 9 sections plus next_action, already parsed.
    """
    provider_name = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    provider_fn = _PROVIDERS.get(provider_name)
    if provider_fn is None:
        raise ExtractionError(f"Unknown LLM_PROVIDER: {provider_name!r}")

    transcript = _messages_to_transcript(messages)
    if not transcript.strip():
        raise ExtractionError("No message content to extract from")

    raw = provider_fn(transcript)
    parsed = _parse_llm_json(raw)

    result: Dict[str, Any] = {}
    for section in SECTIONS:
        val = parsed.get(section, [])
        result[section] = val if isinstance(val, list) else ([val] if val else [])
    result["next_action"] = parsed.get("next_action") or None

    return result


def build_context_package(extracted: Dict[str, Any]) -> str:
    def fmt_list(items: List[str]) -> str:
        return "\n".join(f"- {i}" for i in items) if items else "None"

    next_action = extracted.get("next_action") or "None flagged"

    return f"""Please continue from where we left off.

Goal:
{fmt_list(extracted.get('goals', []))}

Where things stand:
{fmt_list(extracted.get('current_state', []))}

Decided:
{fmt_list(extracted.get('decisions', []))}

Already done:
{fmt_list(extracted.get('completed_work', []))}

Still to do:
{fmt_list(extracted.get('tasks', []))}

Constraints:
{fmt_list(extracted.get('constraints', []))}

Key facts:
{fmt_list(extracted.get('key_facts', []))}

Open questions:
{fmt_list(extracted.get('open_questions', []))}

Next step:
{next_action}"""


def total_items_extracted(extracted: Dict[str, Any]) -> int:
    count = sum(len(extracted.get(s, [])) for s in SECTIONS)
    if extracted.get("next_action"):
        count += 1
    return count
