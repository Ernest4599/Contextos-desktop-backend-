import requests

from services.link_detector import detect_platform
from services.conversation_fetcher import fetch_conversation_text, FetchError
from services.chatgpt_parser import parse_chatgpt_share


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
    """
    Runs the import pipeline:
    - check network
    - check link is supported
    - fetch the conversation, using a platform-specific parser where
      we have a proven one (ChatGPT), falling back to the generic
      tiered fetch otherwise.

    Returns a list of {"role": ..., "text": ...} messages.
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

    if platform == "chatgpt":
        try:
            messages = parse_chatgpt_share(url)
            print(f"[IMPORT] ChatGPT parser succeeded, {len(messages)} messages")
        except Exception as e:
            print(f"[IMPORT] ChatGPT parser failed: {e}")
            raise ShareLinkError("Couldn't read this link")

        if not messages:
            raise ShareLinkError("Couldn't read this link")

        return messages

    # Fallback for platforms without a dedicated parser yet
    try:
        text = await fetch_conversation_text(url)
        print(f"[IMPORT] Generic fetch succeeded, text length: {len(text)}")
    except FetchError as e:
        print(f"[IMPORT] Generic fetch failed: {e}")
        raise ShareLinkError("Couldn't read this link")

    if not text:
        print("[IMPORT] Generic fetch returned empty text")
        raise ShareLinkError("Couldn't read this link")

    return [{"role": "raw", "text": text}]
