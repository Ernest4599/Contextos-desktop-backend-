from urllib.parse import urlparse

SUPPORTED_PLATFORMS = {
    "chatgpt.com": "chatgpt",
    "chat.openai.com": "chatgpt",
    "claude.ai": "claude",
    "share.gemini.google": "gemini",
    "gemini.google.com": "gemini",
    "copilot.microsoft.com": "copilot",
    "grok.com": "grok",
    "chat.deepseek.com": "deepseek",
    "perplexity.ai": "perplexity",
    "meta.ai": "metaai",
}


def detect_platform(url: str) -> str | None:
    """
    Detects which AI platform a share link belongs to.
    Returns the platform name, or None if unsupported.
    """
    try:
        domain = urlparse(url).netloc.lower()
        domain = domain.replace("www.", "")
    except Exception:
        return None

    for known_domain, platform in SUPPORTED_PLATFORMS.items():
        if domain == known_domain or domain.endswith("." + known_domain):
            return platform

    return None


def is_supported_link(url: str) -> bool:
    return detect_platform(url) is not None
