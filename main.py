import base64
from fastapi import FastAPI, Response
from pydantic import BaseModel

from services.share_link_service import import_from_share_link, ShareLinkError
from services.conversation_fetcher import fetch_screenshot_debug

app = FastAPI(title="ContextOS Backend")


@app.get("/")
def read_root():
    return {"status": "ContextOS backend is running"}


class ShareLinkRequest(BaseModel):
    url: str


@app.post("/import/share-link")
async def import_share_link(payload: ShareLinkRequest):
    try:
        text = await import_from_share_link(payload.url)
        return {
            "success": True,
            "text_length": len(text),
            "text_preview": text[:500],
            "full_text": text,
        }
    except ShareLinkError as e:
        return {
            "success": False,
            "error": e.message,
        }


# TEMPORARY DEBUG: renders the page and returns an actual screenshot image
@app.post("/debug/screenshot")
async def debug_screenshot(payload: ShareLinkRequest):
    text, screenshot_b64 = await fetch_screenshot_debug(payload.url)
    image_bytes = base64.b64decode(screenshot_b64)
    return Response(content=image_bytes, media_type="image/png")
