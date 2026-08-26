"""
Validation + content extraction for the "Upload File" input path.
Supports .txt, .md, .json, .html — reuses the existing JSON/HTML
message-shape heuristics for .json and .html.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

from services.json_heuristics import best_message_list
from services.html_parse import parse_html
from services.message_splitter import split_messages

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
SUPPORTED_EXTENSIONS = {".txt", ".md", ".json", ".html"}


class FileExtractionError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _extract_json(raw: str) -> List[Dict[str, str]]:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        raise FileExtractionError("Couldn't find a conversation in this file")

    messages = best_message_list(parsed)
    if not messages:
        raise FileExtractionError("Couldn't find a conversation in this file")

    return [{"role": role, "text": text} for role, text in messages]


def _extract_html(raw: str) -> List[Dict[str, str]]:
    result = parse_html(raw)
    if result is None:
        raise FileExtractionError("Couldn't find a conversation in this file")

    messages, _strategy = result
    return [{"role": role, "text": text} for role, text in messages]


def extract_file_content(filename: str, raw_bytes: bytes) -> List[Dict[str, str]]:
    _, ext = os.path.splitext(filename.lower())

    if ext not in SUPPORTED_EXTENSIONS:
        raise FileExtractionError("This file type isn't supported")

    if not raw_bytes:
        raise FileExtractionError("This file is empty")

    if len(raw_bytes) > MAX_FILE_SIZE:
        raise FileExtractionError("File too large")

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise FileExtractionError("This file is empty")

    if not text.strip():
        raise FileExtractionError("This file is empty")

    if ext == ".json":
        return _extract_json(text)
    if ext == ".html":
        return _extract_html(text)

    return split_messages(text)
