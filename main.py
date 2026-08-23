from fastapi import FastAPI
from pydantic import BaseModel

from services.share_link_service import import_from_share_link, ShareLinkError
from services.chatgpt_parser import parse_chatgpt_share

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


# TEST: new ChatGPT-specific parser based on ChatPeek's proven approach
@app.post("/debug/chatgpt-parse")
async def debug_chatgpt_parse(payload: ShareLinkRequest):
    try:
        messages = parse_chatgpt_share(payload.url)
        return {
            "success": True,
            "message_count": len(messages),
            "messages": messages,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }
