from fastapi import FastAPI, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from curl_cffi import requests as crequests

from services.share_link_service import import_from_share_link, ShareLinkError
from services.paste_validator import validate_pasted_text, PasteValidationError
from services.message_splitter import split_messages
from services.file_extractor import extract_file_content, FileExtractionError
from services.processing_pipeline import run_processing_pipeline

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


class PasteConversationRequest(BaseModel):
    text: str


@app.post("/process/paste")
async def process_paste(payload: PasteConversationRequest):
    try:
        validated = validate_pasted_text(payload.text)
    except PasteValidationError as e:
        return {"success": False, "error": e.message}

    messages = split_messages(validated)
    return StreamingResponse(run_processing_pipeline(messages), media_type="text/event-stream")


@app.post("/process/upload")
async def process_upload(file: UploadFile = File(...)):
    raw_bytes = await file.read()
    try:
        messages = extract_file_content(file.filename, raw_bytes)
    except FileExtractionError as e:
        return {"success": False, "error": e.message}

    return StreamingResponse(run_processing_pipeline(messages), media_type="text/event-stream")


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


class QuickPromptRequest(BaseModel):
    overview: str = ""
    decisions: str = ""
    task: str = ""


@app.post("/quick-prompt")
async def quick_prompt(payload: QuickPromptRequest):
    from services.quick_prompt import generate_quick_prompt, QuickPromptValidationError, QuickPromptError

    try:
        result = generate_quick_prompt(payload.overview, payload.decisions, payload.task)
        return {"success": True, **result}
    except QuickPromptValidationError as e:
        return {"success": False, "error": e.message}
    except QuickPromptError as e:
        return {"success": False, "error": str(e)}


@app.post("/process/share-link")
async def process_share_link(payload: ShareLinkRequest):
    try:
        messages = await import_from_share_link(payload.url)
    except ShareLinkError as e:
        return {"success": False, "error": e.message}

    return StreamingResponse(run_processing_pipeline(messages), media_type="text/event-stream")
