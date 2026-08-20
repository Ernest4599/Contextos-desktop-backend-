from bs4 import BeautifulSoup


class ParseError(Exception):
    """Raised when a conversation's messages cannot be extracted from HTML."""
    pass


def parse_claude(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    messages = []

    for block in soup.select('[data-testid="user-message"], [data-testid="assistant-message"]'):
        role = "user" if block.get("data-testid") == "user-message" else "assistant"
        text = block.get_text(separator="\n", strip=True)
        if text:
            messages.append({"role": role, "text": text})

    if not messages:
        print("=== PARSE ERROR (claude) ===")
        print(f"HTML length: {len(html)}")
        print("First 3000 chars of HTML:")
        print(html[:3000])
        print("=============================")
        raise ParseError("No messages found in Claude share page")

    return messages


def parse_chatgpt(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    messages = []

    for block in soup.select('[data-message-author-role]'):
        role = block.get("data-message-author-role")
        text = block.get_text(separator="\n", strip=True)
        if text and role in ("user", "assistant"):
            messages.append({"role": role, "text": text})

    if not messages:
        print("=== PARSE ERROR (chatgpt) ===")
        print(f"HTML length: {len(html)}")
        print(html[:3000])
        print("==============================")
        raise ParseError("No messages found in ChatGPT share page")

    return messages


def parse_gemini(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    messages = []

    for block in soup.select('.query-text, .response-content'):
        classes = block.get("class", [])
        role = "user" if "query-text" in classes else "assistant"
        text = block.get_text(separator="\n", strip=True)
        if text:
            messages.append({"role": role, "text": text})

    if not messages:
        print("=== PARSE ERROR (gemini) ===")
        print(f"HTML length: {len(html)}")
        print(html[:3000])
        print("=============================")
        raise ParseError("No messages found in Gemini share page")

    return messages


def parse_copilot(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    messages = []

    for block in soup.select('[data-content="user-message"], [data-content="ai-message"]'):
        role = "user" if block.get("data-content") == "user-message" else "assistant"
        text = block.get_text(separator="\n", strip=True)
        if text:
            messages.append({"role": role, "text": text})

    if not messages:
        print("=== PARSE ERROR (copilot) ===")
        print(f"HTML length: {len(html)}")
        print(html[:3000])
        print("==============================")
        raise ParseError("No messages found in Copilot share page")

    return messages


def parse_grok(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    messages = []

    for block in soup.select('[data-testid="user-message"], [data-testid="assistant-message"]'):
        role = "user" if "user" in (block.get("data-testid") or "") else "assistant"
        text = block.get_text(separator="\n", strip=True)
        if text:
            messages.append({"role": role, "text": text})

    if not messages:
        print("=== PARSE ERROR (grok) ===")
        print(f"HTML length: {len(html)}")
        print(html[:3000])
        print("===========================")
        raise ParseError("No messages found in Grok share page")

    return messages


PARSERS = {
    "chatgpt": parse_chatgpt,
    "claude": parse_claude,
    "gemini": parse_gemini,
    "copilot": parse_copilot,
    "grok": parse_grok,
}


def parse_conversation(platform: str, html: str) -> list[dict]:
    parser = PARSERS.get(platform)
    if not parser:
        raise ParseError(f"No parser available for platform: {platform}")
    return parser(html)
