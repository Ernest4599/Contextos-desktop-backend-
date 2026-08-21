import requests

from services.link_detector import detect_platform
from services.conversation_fetcher import fetch_conversation_text, FetchError


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


async def import_from_share_link(url: str) -> str:
    """
    Runs the import pipeline:
    - check network
    - check link is supported
    - fetch the rendered page's visible text

    Returns the raw visible conversation text (a single string).
    Splitting this into individual messages happens later, once we
    can see real output and confirm the actual format each platform
    renders in.
    """
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
        text = await fetch_conversation_text(url)
        print(f"[IMPORT] Fetch succeeded, text length: {len(text)}")
    except FetchError as e:
        print(f"[IMPORT] Fetch failed: {e}")
        raise ShareLinkError("Couldn't read this link")

    if not text:
        print("[IMPORT] Fetch returned empty text")
        raise ShareLinkError("Couldn't read this link")

    return text
