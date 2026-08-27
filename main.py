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

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    except Exception as e:
        print(f"[QUICK_PROMPT] Unexpected error: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}


@app.post("/process/share-link")
async def process_share_link(payload: ShareLinkRequest):
    try:
        messages = await import_from_share_link(payload.url)
    except ShareLinkError as e:
        return {"success": False, "error": e.message}

    return StreamingResponse(run_processing_pipeline(messages), media_type="text/event-stream")


from services.db import get_db_session, init_db
from services.auth_service import signup, login, decode_session_token, AuthError
from fastapi import Header


@app.on_event("startup")
def on_startup():
    try:
        init_db()
    except Exception as e:
        print(f"[DB] Failed to initialize database: {e}")


class SignupRequest(BaseModel):
    email: str
    password: str
    confirm_password: str


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/auth/signup")
def auth_signup(payload: SignupRequest):
    db = None
    try:
        db = get_db_session()
        token, email = signup(db, payload.email, payload.password, payload.confirm_password)
        return {"success": True, "token": token, "email": email}
    except (AuthError, RuntimeError) as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AUTH] Unexpected signup error: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.post("/auth/login")
def auth_login(payload: LoginRequest):
    db = None
    try:
        db = get_db_session()
        token, email = login(db, payload.email, payload.password)
        return {"success": True, "token": token, "email": email}
    except (AuthError, RuntimeError) as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AUTH] Unexpected login error: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.get("/auth/me")
def auth_me(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        return {"success": False, "error": "Not signed in"}
    token = authorization[len("Bearer "):]
    try:
        payload = decode_session_token(token)
        return {"success": True, "email": payload.get("email")}
    except AuthError as e:
        return {"success": False, "error": e.message}


from services.db import get_db_session
from services.auth_service import get_user_id_from_token, AuthError as AiosAuthError
from services import aios_service
from fastapi import Header as AiosHeader


def _require_user(authorization: str) -> int:
    try:
        return get_user_id_from_token(authorization)
    except AiosAuthError as e:
        raise ValueError(e.message)


class TellAiosRequest(BaseModel):
    content: str


class UpdateMemoryRequest(BaseModel):
    content: str


@app.post("/aios/tell")
def aios_tell(payload: TellAiosRequest, authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        result = aios_service.tell_aios(db, user_id, payload.content)
        return {"success": True, **result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except aios_service.AiosError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AIOS] Unexpected error in /aios/tell: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.get("/aios/overview")
def aios_overview(authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        result = aios_service.get_overview(db, user_id)
        return {"success": True, **result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AIOS] Unexpected error in /aios/overview: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.get("/aios/memories")
def aios_memories(category: str | None = None, authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        results = aios_service.get_memories(db, user_id, category)
        return {"success": True, "memories": results}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AIOS] Unexpected error in /aios/memories: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.patch("/aios/memories/{memory_id}")
def aios_update_memory(memory_id: int, payload: UpdateMemoryRequest, authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        result = aios_service.update_memory(db, user_id, memory_id, payload.content)
        return {"success": True, **result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except aios_service.AiosError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AIOS] Unexpected error in PATCH /aios/memories: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.delete("/aios/memories/{memory_id}")
def aios_delete_memory(memory_id: int, authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        aios_service.delete_memory(db, user_id, memory_id)
        return {"success": True}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except aios_service.AiosError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AIOS] Unexpected error in DELETE /aios/memories: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


class AiosQuickPromptRequest(BaseModel):
    message: str


@app.post("/aios/quick-prompt")
def aios_quick_prompt(payload: AiosQuickPromptRequest, authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        result = aios_service.generate_aios_quick_prompt(db, user_id, payload.message)
        return {"success": True, **result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except aios_service.AiosError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AIOS] Unexpected error in /aios/quick-prompt: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


from services import project_service


class CreateProjectRequest(BaseModel):
    name: str


@app.get("/projects")
def get_projects(authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        results = project_service.list_projects(db, user_id)
        return {"success": True, "projects": results}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[PROJECTS] Unexpected error in GET /projects: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.post("/projects")
def post_project(payload: CreateProjectRequest, authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        result = project_service.create_project(db, user_id, payload.name)
        return {"success": True, "project": result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except project_service.ProjectError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[PROJECTS] Unexpected error in POST /projects: {e}")
        return {"success": False, "error": "Couldn't create project"}
    finally:
        if db is not None:
            db.close()


@app.get("/projects/{project_id}")
def get_single_project(project_id: int, authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        result = project_service.get_project(db, user_id, project_id)
        return {"success": True, "project": result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except project_service.ProjectError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[PROJECTS] Unexpected error in GET /projects/id: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.delete("/projects/{project_id}")
def delete_single_project(project_id: int, authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        project_service.delete_project(db, user_id, project_id)
        return {"success": True}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except project_service.ProjectError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[PROJECTS] Unexpected error in DELETE /projects/id: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()
