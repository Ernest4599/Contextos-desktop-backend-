from fastapi import FastAPI
from pydantic import BaseModel

from services.share_link_service import import_from_share_link, ShareLinkError
from services.conversation_fetcher import fetch_conversation_html

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


# TEMPORARY DEBUG ENDPOINT - returns a short summary instead of raw HTML
@app.post("/debug/fetch-html")
async def debug_fetch_html(payload: ShareLinkRequest):
    html = await fetch_conversation_html(payload.url)

    title_start = html.find("<title>")
    title_end = html.find("</title>")
    title = html[title_start + 7:title_end] if title_start != -1 else "Not found"

    return {
        "html_length": len(html),
        "page_title": title,
        "has_cloudflare_challenge": "challenges.cloudflare.com" in html,
        "has_greeting_placeholder": "How can I help you today" in html,
        "has_composer": "composer" in html.lower(),
    }
