from fastapi import FastAPI
from fastapi.responses import HTMLResponse
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


# TEMPORARY DEBUG ENDPOINT - remove once selectors are confirmed
@app.post("/debug/fetch-html", response_class=HTMLResponse)
async def debug_fetch_html(payload: ShareLinkRequest):
    html = await fetch_conversation_html(payload.url)
    return html
