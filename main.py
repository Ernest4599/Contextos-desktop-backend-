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

    # Find all unique data-* attribute names anywhere in the HTML
    data_attrs = sorted(set(re.findall(r'data-[a-z-]+(?==)', html)))
    result["data_attributes_found"] = data_attrs[:40]

    # Find elements whose text content contains "what's up" or "yo" (case-insensitive)
    soup = BeautifulSoup(html, "html.parser")
    hits = []
    for el in soup.find_all(True):
        text = el.get_text(strip=True)
        if text and len(text) < 300 and re.search(r"what.?s up|^\W*yo\W*$", text, re.IGNORECASE):
            hits.append({
                "tag": el.name,
                "attrs": dict(el.attrs),
                "text": text[:100],
            })
            if len(hits) >= 5:
                break
    result["message_element_hits"] = hits

    return result
