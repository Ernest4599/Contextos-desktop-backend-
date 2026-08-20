import requests

from services.link_detector import detect_platform
from services.conversation_fetcher import fetch_conversation_html, FetchError
from services.conversation_parser import parse_conversation, ParseError


class ShareLinkError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def check_network() -> bool:
    try:
        requests.get("https://www.google.com", timeout=5)
        return True
    except requests.RequestException:
        return False


async def import_from_share_link(url: str) -> list[dict]:
    print(f"[IMPORT] Starting import for URL: {url}")

    if not check_network():
        print("[IMPORT] Network check failed")
        raise ShareLinkError("No internet connection")
    print("[IMPORT] Network check passed")

    platform = detect_platform(url)
    if not platform:
        print(f"[IMPORT] Platform not detected for URL: {url}")
        raise ShareLinkError("Unsupported link")
    print(f"[IMPORT] Detected platform: {platform}")

    try:
        html = await fetch_conversation_html(url)
        print(f"[IMPORT] Fetch succeeded, HTML length: {len(html)}")
    except FetchError as e:
        print(f"[IMPORT] Fetch failed: {e}")
        raise ShareLinkError("Couldn't read this link")

    try:
        messages = parse_conversation(platform, html)
        print(f"[IMPORT] Parse succeeded, found {len(messages)} messages")
    except ParseError as e:
        print(f"[IMPORT] Parse failed: {e}")
        raise ShareLinkError("Couldn't read this link")

    return messages
