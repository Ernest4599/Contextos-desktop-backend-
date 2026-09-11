p = "main.py"
s = open(p).read()

old_anchor = '''        print(f"[QUICK_PROMPT] Unexpected error: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}


@app.post("/process/share-link")'''

new_anchor = '''        print(f"[QUICK_PROMPT] Unexpected error: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}


class EditFeedbackRequest(BaseModel):
    original: str
    edited: str


@app.post("/quick-prompt/edit-feedback")
async def quick_prompt_edit_feedback(payload: EditFeedbackRequest, access: AccessContext = Depends(require_access)):
    """
    Best-effort signal for the Quick Prompt edit-feedback loop (algorithm
    steps 45-46). Signed-in only - anonymous/license-only usage is never
    tracked here. Always returns success even if classification or
    storage fails internally, since this must never surface an error to
    the user or affect the Copy action it's called alongside.
    """
    if access.via != "session" or not access.user_id:
        return {"success": True}

    from services.db import get_db_session
    from services import aios_service

    db = get_db_session()
    try:
        aios_service.record_edit_feedback(db, access.user_id, payload.original, payload.edited)
    except Exception as e:
        print(f"[EDIT_FEEDBACK] Failed to record edit feedback: {e}")
    finally:
        db.close()

    return {"success": True}


@app.post("/process/share-link")'''

assert s.count(old_anchor) == 1, "main.py: insertion anchor not found/not unique"
s = s.replace(old_anchor, new_anchor, 1)

open(p, "w").write(s)
print("main.py patched")
