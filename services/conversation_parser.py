from services.html_parse import parse_html


class ParseError(Exception):
    """Raised when a conversation's messages cannot be extracted from HTML."""
    pass


def parse_conversation(platform: str, html: str) -> list[dict]:
    """
    Platform-agnostic parse: searches embedded JSON first (most reliable),
    then falls back to DOM role-attribute scanning.
    """
    result = parse_html(html)
    if result is None:
        raise ParseError(f"No messages found in {platform} share page")

    messages, strategy = result
    print(f"[PARSE] Found {len(messages)} messages using strategy: {strategy}")

    return [{"role": role, "text": content} for role, content in messages]
