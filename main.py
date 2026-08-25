from fastapi import FastAPI
from pydantic import BaseModel
from curl_cffi import requests as crequests

from services.share_link_service import import_from_share_link, ShareLinkError

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


# TEMPORARY DEBUG: test multiple curl_cffi impersonate values from Render itself
@app.post("/debug/impersonate-test")
async def debug_impersonate_test(payload: ShareLinkRequest):
    import re
    match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", payload.url, re.IGNORECASE)
    if not match:
        return {"error": "Could not find a UUID in this URL"}
    uuid = match.group(1)
    api_url = f"https://www.perplexity.ai/rest/thread/{uuid}"

    targets = ["chrome", "chrome110", "chrome120", "chrome123", "chrome124", "chrome131", "safari17_0"]
    results = {}

    for target in targets:
        try:
            r = crequests.get(
                api_url,
                impersonate=target,
                headers={
                    "Accept": "application/json",
                    "Referer": f"https://www.perplexity.ai/search/{uuid}",
                },
                timeout=15,
            )
            results[target] = {"status": r.status_code, "length": len(r.text)}
        except Exception as e:
            results[target] = {"error": str(e)}

    return results
