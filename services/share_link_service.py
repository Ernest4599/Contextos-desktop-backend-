import requests

from services.link_detector import detect_platform
from services.conversation_fetcher import fetch_conversation_html, FetchError
from services.conversation_parser import parse_conversation, ParseError


class ShareLinkError(Exception):
    """Base error for the share link import flow. Carries a user-facing message."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def check_network() -> bool:
    """
    Basic network availability check.
    """
    try:
        requests.get("https://www.google.com", timeout=5)
        return True
    except requests.RequestException:
        return False


async def import_from_share_link(url: str) -> list[dict]:
    """
    Runs steps 2-5 of the import pipeline:
    - check network
    - check link is supported
    - fetch the conversation
    - split into messages

    Returns a list of {"role": ..., "text": ...} messages.
    Raises ShareLinkError with a user-facing message on any failure.
    """
    if not check_network():
        raise ShareLinkError("No internet connection")

    platform = detect_platform(url)
    if not platform:
        raise ShareLinkError("Unsupported link")

    try:
        html = await fetch_conversation_html(url)
    except FetchError:
        raise ShareLinkError("Couldn't read this link")

    try:
        messages = parse_conversation(platform, html)
    except ParseError:
        raise ShareLinkError("Couldn't read this link")

    return messages
