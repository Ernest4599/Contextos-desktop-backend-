from fastapi import FastAPI
from pydantic import BaseModel

from services.share_link_service import import_from_share_link, ShareLinkError
from services.gemini_sniffer import sniff_gemini_requests

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


# TEMPORARY DEBUG: capture the real batchexecute request Gemini's page makes
@app.post("/debug/gemini-sniff")
async def debug_gemini_sniff(payload: ShareLinkRequest):
    captured = await sniff_gemini_requests(payload.url)
    return {"captured_requests": captured, "count": len(captured)}
