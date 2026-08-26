"""
Validation for the "Paste Conversation" input path.
"""
from __future__ import annotations

MIN_LENGTH = 20
MAX_LENGTH = 100_000  # characters


class PasteValidationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def validate_pasted_text(text: str) -> str:
    if text is None or not text.strip():
        raise PasteValidationError("Please paste a conversation")

    stripped = text.strip()

    if len(stripped) < MIN_LENGTH:
        raise PasteValidationError("That doesn't look like a conversation")

    if len(stripped) > MAX_LENGTH:
        raise PasteValidationError("Conversation too long — try trimming it")

    return stripped
