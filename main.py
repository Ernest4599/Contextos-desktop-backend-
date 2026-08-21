from fastapi import FastAPI
from pydantic import BaseModel
from bs4 import BeautifulSoup
import re

from services.share_link_service import import_from_share_link, ShareLinkError
from services.conversation_fetcher import fetch_conversation_html
from services.conversation_parser import parse_conversation, ParseError
from services.link_detector import detect_platform

app = FastAPI(title="ContextOS Backend")


@app.get("/")
def read_root():
    return {"status": "ContextOS backend is running"}


class ShareLinkRequest(BaseModel):
    url: str


@app.post("/import/share-link")
async def import_share_link(payload: ShareLinkRequest):
    try:
        messages = await import_from_share_link(payload.url)
        return {
            "success": True,
            "message_count": len(messages),
            "messages": messages,
        }
    except ShareLinkError as e:
        return {
            "success": False,
            "error": e.message,
        }


# TEMPORARY DEBUG ENDPOINT
@app.post("/debug/fetch-html")
async def debug_fetch_html(payload: ShareLinkRequest):
    html = await fetch_conversation_html(payload.url)
    platform = detect_platform(payload.url)

    title_start = html.find("<title>")
    title_end = html.find("</title>")
    title = html[title_start + 7:title_end] if title_start != -1 else "Not found"

    result = {
        "html_length": len(html),
        "page_title": title,
        "platform": platform,
        "has_cloudflare_challenge": "challenges.cloudflare.com" in html,
    }

    try:
        messages = parse_conversation(platform, html)
        result["parse_success"] = True
        result["message_count"] = len(messages)
        result["first_message_preview"] = messages[0]["text"][:200] if messages else None
    except ParseError as e:
        result["parse_success"] = False
        result["parse_error"] = str(e)

    data_attrs = sorted(set(re.findall(r'data-[a-z-]+(?==)', html)))
    result["data_attributes_found"] = data_attrs[:40]

    soup = BeautifulSoup(html, "html.parser")

    # Total visible text on the page (a rough gauge of "is there real content
    # rendered here, or just an empty app shell?")
    body = soup.find("body")
    body_text = body.get_text(separator=" ", strip=True) if body else ""
    result["body_visible_text_length"] = len(body_text)
    result["body_visible_text_preview"] = body_text[:500]

    # Search for distinctive words from the page's own title inside the body.
    # If the title reflects the real conversation but these words are absent
    # from the body, the actual message content never rendered into the DOM.
    title_words = [w.strip("@.,!?") for w in title.split() if len(w.strip("@.,!?")) > 4]
    title_words = [w for w in title_words if w.lower() not in ("chatgpt", "claude", "gemini")]
    word_hits = {}
    for word in title_words[:5]:
        word_hits[word] = word.lower() in body_text.lower()
    result["title_words_found_in_body"] = word_hits

    return result
