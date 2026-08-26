"""
Splits raw conversation text (from paste or .txt/.md file upload) into a
list of {role, text} messages. Looks for speaker-label lines (e.g.
"User:", "ChatGPT:", "**Claude:**"); if fewer than 2 labeled messages are
found, the whole text is kept as a single block so downstream extraction
still has full content to work with.
"""
from __future__ import annotations

import re
from typing import Dict, List

from services.json_heuristics import ROLE_ALIASES

_LABEL_RE = re.compile(
    r"^\**\s*(" + "|".join(re.escape(k) for k in ROLE_ALIASES.keys()) + r")\s*\**\s*:\s*",
    re.IGNORECASE,
)


def split_messages(text: str) -> List[Dict[str, str]]:
    lines = text.splitlines()
    messages: List[Dict[str, str]] = []
    current_role = None
    current_lines: List[str] = []

    def flush():
        if current_role is not None and current_lines:
            content = "\n".join(current_lines).strip()
            if content:
                messages.append({"role": current_role, "text": content})

    for line in lines:
        match = _LABEL_RE.match(line)
        if match:
            flush()
            current_role = ROLE_ALIASES.get(match.group(1).lower(), "user")
            current_lines = [line[match.end():]]
        else:
            current_lines.append(line)
    flush()

    if len(messages) >= 2:
        return messages

    stripped = text.strip()
    return [{"role": "conversation", "text": stripped}] if stripped else []
