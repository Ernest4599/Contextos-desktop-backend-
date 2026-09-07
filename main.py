from sqlalchemy.sql import func
from fastapi import FastAPI, UploadFile, File, Cookie, Response, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from curl_cffi import requests as crequests

from services.share_link_service import import_from_share_link, ShareLinkError
from services.paste_validator import validate_pasted_text, PasteValidationError
from services.message_splitter import split_messages
from services.file_extractor import extract_file_content, FileExtractionError
from services.processing_pipeline import run_processing_pipeline
from services.access_control import require_access, AccessContext
from services.admin_access import require_admin
from services.models import User, AiosMemory, ContextPackage, SecurityEvent, License, Project, LLMProviderEvent

import json


async def _pipeline_with_autosave(messages, access: AccessContext, source: str, is_metered: bool = False, license_id: int | None = None, allowed_providers: list[str] | None = None):
    """
    Wraps run_processing_pipeline to auto-save the resulting Context
    Package for signed-in users, without processing_pipeline.py itself
    needing to know about persistence. Every event is passed through to
    the client unchanged; only the "complete" event is inspected.
    """
    from services.processing_pipeline import run_processing_pipeline

    async for chunk in run_processing_pipeline(messages, allowed_providers=allowed_providers):
        if access.via == "session" and access.user_id and chunk.startswith("event: complete"):
            try:
                data_line = next(line for line in chunk.split("\n") if line.startswith("data:"))
                data = json.loads(data_line[len("data:"):].strip())
                package_content = data.get("context_package", "")
            except (StopIteration, json.JSONDecodeError):
                package_content = ""

            if package_content:
                from services.db import get_db_session
                from services import package_service

                db = None
                try:
                    db = get_db_session()
                    title = package_content.strip().split("\n")[0][:80] or "Context Package"
                    package_service.save_package(
                        db, access.user_id, source=source, title=title, content=package_content
                    )
                except Exception as e:
                    print(f"[PACKAGES] Failed to auto-save {source} package: {e}")
                finally:
                    if db is not None:
                        db.close()

        if access.via == "free" and access.installation_id:
            if chunk.startswith("event: complete"):
                from services.db import get_db_session
                from services import free_license_service

                db = None
                try:
                    db = get_db_session()
                    free_license_service.commit_import(db, access.installation_id)
                except Exception as e:
                    print(f"[FREE_LICENSE] Failed to commit import: {e}")
                finally:
                    if db is not None:
                        db.close()
            elif chunk.startswith("event: error"):
                from services.db import get_db_session
                from services import free_license_service

                db = None
                try:
                    db = get_db_session()
                    free_license_service.release_import(db, access.installation_id)
                except Exception as e:
                    print(f"[FREE_LICENSE] Failed to release import: {e}")
                finally:
                    if db is not None:
                        db.close()

        if is_metered and license_id:
            from services.db import get_db_session
            from services import license_service

            if chunk.startswith("event: complete"):
                db = None
                try:
                    db = get_db_session()
                    license_service.commit_credits(db, license_id)
                except Exception as e:
                    print(f"[LICENSE] Failed to commit credits: {e}")
                finally:
                    if db is not None:
                        db.close()
            elif chunk.startswith("event: error"):
                db = None
                try:
                    db = get_db_session()
                    license_service.release_credits(db, license_id)
                except Exception as e:
                    print(f"[LICENSE] Failed to release credits: {e}")
                finally:
                    if db is not None:
                        db.close()

        yield chunk


app = FastAPI(title="ContextOS Backend")


@app.get("/admin/whoami")
def admin_whoami(admin_user_id: int = Depends(require_admin)):
    """
    Phase 1 test route: confirms the admin auth pipeline works end to
    end before any real admin data endpoints are built on top of it.
    """
    return {"success": True, "user_id": admin_user_id, "is_admin": True}

from fastapi.middleware.cors import CORSMiddleware
from services.security_headers import SecurityHeadersMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from services.rate_limit import limiter

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SecurityHeadersMiddleware)

app.state.limiter = limiter
def _log_rate_limit_and_respond(request: Request, exc: RateLimitExceeded):
    db = get_db_session()
    try:
        client_ip = request.client.host if request.client else None
        ip_hash = recovery_service.hash_ip(client_ip)

        user_id = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                token_payload = decode_session_token(auth_header[len("Bearer "):])
                user_id = int(token_payload["sub"])
            except AuthError:
                pass

        db.add(SecurityEvent(
            event_type="RATE_LIMIT_EXCEEDED",
            user_id=user_id,
            success=False,
            ip_hash=ip_hash,
            detail=str(request.url.path),
        ))
        db.commit()
    except Exception as e:
        print(f"[SECURITY] Failed to log rate limit event: {e}")
    finally:
        db.close()

    return _rate_limit_exceeded_handler(request, exc)


app.add_exception_handler(RateLimitExceeded, _log_rate_limit_and_respond)
app.add_middleware(SlowAPIMiddleware)


@app.get("/admin/overview")
def admin_overview(admin_user_id: int = Depends(require_admin)):
    db = get_db_session()
    try:
        total_events = db.query(SecurityEvent).count()
        failed_events = db.query(SecurityEvent).filter(SecurityEvent.success.is_(False)).count()
        error_rate = round(failed_events / total_events * 100, 2) if total_events > 0 else None

        return {
            # Genuinely-real metrics
            "users": db.query(User).count(),
            "aios_activity": db.query(AiosMemory).count(),

            # Privacy-safe counters -- aggregate totals only, no per-user data
            "context_packages": db.query(ContextPackage).count(),
            "error_rate": error_rate,  # failed / total security events, all-time

            # Placeholder -- wired up in Phase 4
            "processing_jobs": None,
        }
    finally:
        db.close()


@app.get("/admin/security-events")
def admin_security_events(
    admin_user_id: int = Depends(require_admin),
    limit: int = 50,
    offset: int = 0,
    event_type: str | None = None,
):
    limit = max(1, min(limit, 200))
    db = get_db_session()
    try:
        query = db.query(SecurityEvent, User.email).outerjoin(User, SecurityEvent.user_id == User.id)
        if event_type:
            query = query.filter(SecurityEvent.event_type == event_type)

        total = query.count()
        rows = (
            query.order_by(SecurityEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        events = [
            {
                "id": event.id,
                "event_type": event.event_type,
                "user_id": event.user_id,
                "user_email": email,
                "success": event.success,
                "ip_hash": event.ip_hash,
                "detail": event.detail,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event, email in rows
        ]

        return {"events": events, "total": total, "limit": limit, "offset": offset}
    finally:
        db.close()


@app.get("/admin/users")
def admin_list_users(
    admin_user_id: int = Depends(require_admin),
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    Unified list of every account and license-only holder. Two genuinely
    different kinds of record are merged into one shape so the admin UI
    can show them in a single, sortable, paginated list:
      - "account": a row from `users` (email, password, may have a license)
      - "license_only": a standalone License with no linked user_id -
        per the no-account architecture, this holder has no email or
        identity at all, just a license key.
    Combined in Python rather than a SQL UNION since the two source
    tables have almost nothing in common to join on.
    """
    limit = max(1, min(limit, 200))
    db = get_db_session()
    try:
        user_query = db.query(User)
        if search:
            user_query = user_query.filter(User.email.ilike(f"%{search}%"))
        accounts = user_query.all()

        license_query = db.query(License).filter(License.user_id.is_(None))
        if search:
            license_query = license_query.filter(License.license_key.ilike(f"%{search}%"))
        standalone_licenses = license_query.all()

        combined = [
            {
                "type": "account",
                "id": u.id,
                "email": u.email,
                "license_key": None,
                "plan": None,
                "status": None,
                "is_admin": u.is_admin,
                "created_at": u.created_at,
                "last_login_at": u.last_login_at,
            }
            for u in accounts
        ] + [
            {
                "type": "license_only",
                "id": None,
                "email": None,
                "license_key": lic.license_key,
                "plan": lic.plan,
                "status": lic.status,
                "is_admin": False,
                "created_at": lic.created_at,
                "last_login_at": None,
            }
            for lic in standalone_licenses
        ]

        from datetime import datetime as _dt, timezone as _tz
        _epoch = _dt(1970, 1, 1, tzinfo=_tz.utc)
        combined.sort(key=lambda row: row["created_at"] or _epoch, reverse=True)
        total = len(combined)
        page = combined[offset:offset + limit]

        return {
            "users": [
                {
                    **row,
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "last_login_at": row["last_login_at"].isoformat() if row["last_login_at"] else None,
                }
                for row in page
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        db.close()


@app.get("/admin/users/{user_id}")
def admin_get_user(user_id: int, admin_user_id: int = Depends(require_admin)):
    db = get_db_session()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return {"success": False, "error": "User not found"}

        try:
            license = license_service.get_license_for_user(db, user_id)
        except license_service.LicenseError:
            license = None

        return {
            "success": True,
            "user": {
                "id": user.id,
                "email": user.email,
                "is_admin": user.is_admin,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            },
            "license": license,
            "activity": {
                "context_packages": db.query(ContextPackage).filter(ContextPackage.user_id == user_id).count(),
                "aios_memories": db.query(AiosMemory).filter(AiosMemory.user_id == user_id, AiosMemory.status == "active").count(),
                "projects": db.query(Project).filter(Project.user_id == user_id).count(),
            },
        }
    finally:
        db.close()


class SetAdminRoleRequest(BaseModel):
    is_admin: bool


@app.patch("/admin/users/{user_id}/admin")
def admin_set_admin_role(
    user_id: int,
    payload: SetAdminRoleRequest,
    admin_user_id: int = Depends(require_admin),
):
    if user_id == admin_user_id and not payload.is_admin:
        return {"success": False, "error": "Cannot remove your own admin access"}

    db = get_db_session()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return {"success": False, "error": "User not found"}

        user.is_admin = payload.is_admin
        db.add(user)
        db.commit()
        return {"success": True, "id": user.id, "is_admin": user.is_admin}
    finally:
        db.close()


@app.post("/admin/users/{user_id}/license/revoke")
def admin_revoke_license(user_id: int, admin_user_id: int = Depends(require_admin)):
    db = get_db_session()
    try:
        license = (
            db.query(License)
            .filter(License.user_id == user_id, License.status == "active")
            .order_by(License.created_at.desc())
            .first()
        )
        if not license:
            return {"success": False, "error": "No active license found for this user"}

        license.status = "revoked"
        db.add(license)
        db.commit()
        return {"success": True, "license_id": license.id, "status": "revoked"}
    finally:
        db.close()


class AdminRevealLicenseRequest(BaseModel):
    password: str


@app.post("/admin/users/{user_id}/license/reveal")
def admin_reveal_license(
    user_id: int,
    payload: AdminRevealLicenseRequest,
    request: Request,
    admin_user_id: int = Depends(require_admin),
):
    """Admin re-enters THEIR OWN password (not the target user's) to
    reveal the target user's license key. Logged with both admin and
    target IDs for audit."""
    db = get_db_session()
    try:
        if not verify_user_password(db, admin_user_id, payload.password):
            return {"success": False, "error": "Incorrect password"}

        try:
            license = license_service.get_raw_license_for_user(db, user_id)
        except license_service.LicenseError as e:
            return {"success": False, "error": e.message}

        client_ip = request.client.host if request.client else None
        ip_hash = recovery_service.hash_ip(client_ip)
        db.add(SecurityEvent(
            event_type="ADMIN_LICENSE_KEY_REVEALED",
            user_id=admin_user_id,
            success=True,
            ip_hash=ip_hash,
            detail=f"target_user_id={user_id}",
        ))
        db.commit()

        return {"success": True, "license_key": license["license_key"]}
    finally:
        db.close()


@app.get("/admin/integrations")
def admin_integrations(admin_user_id: int = Depends(require_admin)):
    from sqlalchemy import func as sqla_func

    db = get_db_session()
    try:
        rows = (
            db.query(
                LLMProviderEvent.provider,
                LLMProviderEvent.success,
                sqla_func.count(LLMProviderEvent.id),
            )
            .group_by(LLMProviderEvent.provider, LLMProviderEvent.success)
            .all()
        )

        stats: dict[str, dict[str, int]] = {}
        for provider, success, count in rows:
            entry = stats.setdefault(provider, {"success": 0, "failure": 0})
            entry["success" if success else "failure"] = count

        providers = [
            {
                "provider": provider,
                "success": counts["success"],
                "failure": counts["failure"],
                "total": counts["success"] + counts["failure"],
            }
            for provider, counts in stats.items()
        ]

        return {"providers": providers}
    finally:
        db.close()


@app.get("/admin/integrations/events")
def admin_integration_events(
    admin_user_id: int = Depends(require_admin),
    limit: int = 50,
    offset: int = 0,
    provider: str | None = None,
):
    limit = max(1, min(limit, 200))
    db = get_db_session()
    try:
        query = db.query(LLMProviderEvent)
        if provider:
            query = query.filter(LLMProviderEvent.provider == provider)

        total = query.count()
        rows = (
            query.order_by(LLMProviderEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        events = [
            {
                "id": e.id,
                "provider": e.provider,
                "success": e.success,
                "error_message": e.error_message,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in rows
        ]

        return {"events": events, "total": total, "limit": limit, "offset": offset}
    finally:
        db.close()


@app.get("/admin/active-users")
def admin_active_users(admin_user_id: int = Depends(require_admin)):
    """
    Daily distinct-user login counts, grouped by calendar day (UTC),
    covering the full range of available data -- no fixed window.
    Sourced from SecurityEvent LOGIN_SUCCESS rows, so this only has
    history back to when Phase 3 logging shipped, not further.
    """
    from sqlalchemy import func as sqla_func

    db = get_db_session()
    try:
        day_col = sqla_func.date(SecurityEvent.created_at)
        rows = (
            db.query(day_col.label("day"), sqla_func.count(sqla_func.distinct(SecurityEvent.user_id)))
            .filter(SecurityEvent.event_type == "LOGIN_SUCCESS")
            .group_by(day_col)
            .order_by(day_col)
            .all()
        )

        daily = [{"date": str(day), "active_users": count} for day, count in rows]
        return {"daily": daily}
    finally:
        db.close()


@app.get("/")
def read_root():
    return {"status": "ContextOS backend is running"}


class ShareLinkRequest(BaseModel):
    url: str


@app.post("/import/share-link")
async def import_share_link(payload: ShareLinkRequest, access: AccessContext = Depends(require_access)):
    from services.db import get_db_session
    from services import free_license_service
    from services import license_service

    is_metered = access.plan in license_service.PLAN_CREDIT_LIMITS and access.license_id is not None
    reserved = False

    if access.via == "free" and access.installation_id:
        db = get_db_session()
        try:
            free_license_service.check_and_reserve_import(db, access.installation_id)
        except free_license_service.FreeLicenseError as e:
            return {"success": False, "error": e.message}
        finally:
            db.close()
    elif is_metered:
        db = get_db_session()
        try:
            license_service.check_and_reserve_credits(db, access.license_id)
            reserved = True
        except license_service.LicenseError as e:
            return {"success": False, "error": e.message}
        finally:
            db.close()

    try:
        messages = await import_from_share_link(payload.url)

        if access.via == "free" and access.installation_id:
            db = get_db_session()
            try:
                free_license_service.commit_import(db, access.installation_id)
            finally:
                db.close()
        elif is_metered and reserved:
            db = get_db_session()
            try:
                license_service.commit_credits(db, access.license_id)
            finally:
                db.close()

        return {
            "success": True,
            "message_count": len(messages),
            "messages": messages,
        }
    except ShareLinkError as e:
        if access.via == "free" and access.installation_id:
            db = get_db_session()
            try:
                free_license_service.release_import(db, access.installation_id)
            finally:
                db.close()
        elif is_metered and reserved:
            db = get_db_session()
            try:
                license_service.release_credits(db, access.license_id)
            finally:
                db.close()
        return {
            "success": False,
            "error": e.message,
        }


class PasteConversationRequest(BaseModel):
    text: str


@app.post("/process/paste")
async def process_paste(payload: PasteConversationRequest, access: AccessContext = Depends(require_access)):
    try:
        validated = validate_pasted_text(payload.text)
    except PasteValidationError as e:
        return {"success": False, "error": e.message}

    from services import license_service

    is_metered = False
    if access.via == "free":
        from services.db import get_db_session
        from services import free_license_service

        db = get_db_session()
        try:
            free_license_service.check_and_reserve_import(db, access.installation_id)
        except free_license_service.FreeLicenseError as e:
            return {"success": False, "error": e.message}
        finally:
            db.close()
    else:
        from services.db import get_db_session

        is_metered = access.plan in license_service.PLAN_CREDIT_LIMITS and access.license_id is not None
        if is_metered:
            db = get_db_session()
            try:
                license_service.check_and_reserve_credits(db, access.license_id)
            except license_service.LicenseError as e:
                return {"success": False, "error": e.message}
            finally:
                db.close()

    messages = split_messages(validated)
    allowed_providers = license_service.PLAN_PROVIDERS.get(access.plan)
    return StreamingResponse(
        _pipeline_with_autosave(messages, access, source="import", is_metered=is_metered, license_id=access.license_id, allowed_providers=allowed_providers),
        media_type="text/event-stream",
    )


@app.post("/process/upload")
async def process_upload(file: UploadFile = File(...), access: AccessContext = Depends(require_access)):
    raw_bytes = await file.read()
    try:
        messages = extract_file_content(file.filename, raw_bytes)
    except FileExtractionError as e:
        return {"success": False, "error": e.message}

    from services import license_service

    is_metered = False
    if access.via == "free":
        from services.db import get_db_session
        from services import free_license_service

        db = get_db_session()
        try:
            free_license_service.check_and_reserve_import(db, access.installation_id)
        except free_license_service.FreeLicenseError as e:
            return {"success": False, "error": e.message}
        finally:
            db.close()
    else:
        from services.db import get_db_session

        is_metered = access.plan in license_service.PLAN_CREDIT_LIMITS and access.license_id is not None
        if is_metered:
            db = get_db_session()
            try:
                license_service.check_and_reserve_credits(db, access.license_id)
            except license_service.LicenseError as e:
                return {"success": False, "error": e.message}
            finally:
                db.close()

    allowed_providers = license_service.PLAN_PROVIDERS.get(access.plan)
    return StreamingResponse(
        _pipeline_with_autosave(messages, access, source="import", is_metered=is_metered, license_id=access.license_id, allowed_providers=allowed_providers),
        media_type="text/event-stream",
    )


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
async def quick_prompt(payload: QuickPromptRequest, access: AccessContext = Depends(require_access)):
    from services.quick_prompt import generate_quick_prompt, QuickPromptValidationError, QuickPromptError
    from services.db import get_db_session
    from services import package_service
    from services import license_service

    if access.via == "free" or access.plan == "free":
        return {
            "success": False,
            "error": "Quick Prompt isn't available on the free plan. Upgrade to Pro to unlock it.",
            "upgrade_required": True,
        }

    is_metered = access.plan in license_service.PLAN_CREDIT_LIMITS and access.license_id is not None
    reserved = False

    try:
        if is_metered:
            credit_db = get_db_session()
            try:
                license_service.check_and_reserve_credits(credit_db, access.license_id)
                reserved = True
            except license_service.LicenseError as e:
                return {"success": False, "error": e.message}
            finally:
                credit_db.close()

        allowed_providers = license_service.PLAN_PROVIDERS.get(access.plan)
        result = generate_quick_prompt(payload.overview, payload.decisions, payload.task, allowed_providers=allowed_providers)

        if is_metered:
            commit_db = get_db_session()
            try:
                license_service.commit_credits(commit_db, access.license_id)
            finally:
                commit_db.close()

        if access.via == "session" and access.user_id and result.get("prompt"):
            db = None
            try:
                db = get_db_session()
                title = (payload.task or "Quick Prompt").strip() or "Quick Prompt"
                package_service.save_package(
                    db, access.user_id, source="quick_prompt", title=title, content=result["prompt"]
                )
            except Exception as e:
                print(f"[PACKAGES] Failed to auto-save quick-prompt package: {e}")
            finally:
                if db is not None:
                    db.close()

        return {"success": True, **result}
    except QuickPromptValidationError as e:
        if is_metered and reserved:
            release_db = get_db_session()
            try:
                license_service.release_credits(release_db, access.license_id)
            finally:
                release_db.close()
        return {"success": False, "error": e.message}
    except QuickPromptError as e:
        if is_metered and reserved:
            release_db = get_db_session()
            try:
                license_service.release_credits(release_db, access.license_id)
            finally:
                release_db.close()
        return {"success": False, "error": str(e)}
    except Exception as e:
        if is_metered and reserved:
            release_db = get_db_session()
            try:
                license_service.release_credits(release_db, access.license_id)
            finally:
                release_db.close()
        print(f"[QUICK_PROMPT] Unexpected error: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}


@app.post("/process/share-link")
async def process_share_link(payload: ShareLinkRequest, access: AccessContext = Depends(require_access)):
    try:
        messages = await import_from_share_link(payload.url)
    except ShareLinkError as e:
        return {"success": False, "error": e.message}

    if access.via == "free":
        from services.db import get_db_session
        from services import free_license_service

        db = get_db_session()
        try:
            free_license_service.check_and_reserve_import(db, access.installation_id)
        except free_license_service.FreeLicenseError as e:
            return {"success": False, "error": e.message}
        finally:
            db.close()

    return StreamingResponse(_pipeline_with_autosave(messages, access, source="import"), media_type="text/event-stream")


from services.db import get_db_session, init_db
from services.auth_service import signup, login, decode_session_token, AuthError, verify_user_password
from services import terms_service
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
def auth_signup(payload: SignupRequest, contextos_anon_id: str | None = Cookie(default=None)):
    db = None
    try:
        db = get_db_session()
        token, email = signup(db, payload.email, payload.password, payload.confirm_password)
        payload_data = decode_session_token(token)
        terms_service.link_anon_to_user(db, contextos_anon_id, int(payload_data["sub"]))
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
def auth_login(payload: LoginRequest, request: Request, contextos_anon_id: str | None = Cookie(default=None)):
    db = None
    try:
        db = get_db_session()
        client_ip = request.client.host if request.client else None
        ip_hash = recovery_service.hash_ip(client_ip)

        try:
            token, email = login(db, payload.email, payload.password)
        except AuthError:
            db.add(SecurityEvent(event_type="LOGIN_FAILURE", user_id=None, success=False, ip_hash=ip_hash))
            db.commit()
            raise

        payload_data = decode_session_token(token)
        user_id = int(payload_data["sub"])

        db.query(User).filter(User.id == user_id).update({User.last_login_at: func.now()})
        db.add(SecurityEvent(event_type="LOGIN_SUCCESS", user_id=user_id, success=True, ip_hash=ip_hash))
        db.commit()

        terms_service.link_anon_to_user(db, contextos_anon_id, user_id)
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
    except AuthError as e:
        return {"success": False, "error": e.message}

    is_admin = False
    db = get_db_session()
    try:
        user = db.query(User).filter(User.id == int(payload["sub"])).first()
        is_admin = bool(user and user.is_admin)
    finally:
        db.close()

    return {"success": True, "email": payload.get("email"), "is_admin": is_admin}


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

        if result.get("prompt"):
            try:
                title = (payload.message or "AIOS Quick Prompt").strip() or "AIOS Quick Prompt"
                package_service.save_package(
                    db, user_id, source="aios_quick_prompt", title=title, content=result["prompt"]
                )
            except Exception as e:
                print(f"[PACKAGES] Failed to auto-save aios-quick-prompt package: {e}")

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


from services import package_service


@app.get("/packages")
def get_packages(authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        results = package_service.list_packages(db, user_id)
        return {"success": True, "packages": results}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[PACKAGES] Unexpected error in GET /packages: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.delete("/packages/{package_id}")
def delete_single_package(package_id: int, authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        package_service.delete_package(db, user_id, package_id)
        return {"success": True}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except package_service.PackageError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[PACKAGES] Unexpected error in DELETE /packages/id: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.post("/packages/clear")
def clear_all_packages(authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        package_service.clear_packages(db, user_id)
        return {"success": True}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[PACKAGES] Unexpected error in POST /packages/clear: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


from services import license_service


class PurchaseLicenseRequest(BaseModel):
    plan: str


@app.post("/license/purchase")
def purchase_license(
    payload: PurchaseLicenseRequest,
    request: Request,
    response: Response,
    authorization: str = AiosHeader(default=""),
    contextos_installation_id: str | None = Cookie(default=None),
):
    """
    Stubbed purchase endpoint: skips real payment for now and immediately
    creates an active license linked to the authenticated account, or
    with no account if not signed in (standalone path completed later
    once recovery codes exist). Anonymous creation is guarded by the
    same installation_id cookie the free tier uses (issued here if it
    doesn't exist yet) plus a per-IP daily cap - see
    check_anonymous_creation_allowed in license_service.py.
    """
    db = None
    try:
        try:
            user_id = _require_user(authorization)
        except ValueError:
            user_id = None

        db = get_db_session()

        installation_id = None
        ip_hash = None
        if user_id is None:
            installation_id = (contextos_installation_id or "").strip()
            if not installation_id:
                import secrets
                installation_id = secrets.token_urlsafe(24)
                response.set_cookie(
                    key=INSTALLATION_ID_COOKIE,
                    value=installation_id,
                    httponly=True,
                    secure=True,
                    samesite="none",
                    max_age=60 * 60 * 24 * 365,
                )
            client_ip = request.client.host if request.client else None
            ip_hash = recovery_service.hash_ip(client_ip) if client_ip else None
            license_service.check_anonymous_creation_allowed(db, installation_id, ip_hash)

        result = license_service.create_license_after_payment(
            db, user_id, payload.plan, installation_id=installation_id, ip_hash=ip_hash
        )
        return {"success": True, "license": result}
    except license_service.LicenseError as e:
        return {"success": False, "error": e.message}
    except Exception as e:
        print(f"[LICENSE] Unexpected error in /license/purchase: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.get("/license/mine")
def get_my_license(authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        result = license_service.get_license_for_user(db, user_id)
        return {"success": True, "license": result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except license_service.LicenseError as e:
        return {"success": False, "error": e.message}
    except Exception as e:
        print(f"[LICENSE] Unexpected error in /license/mine: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


from fastapi import Request
from services import recovery_service


@app.post("/license/purchase-with-codes")
def purchase_license_with_codes(
    payload: PurchaseLicenseRequest,
    request: Request,
    response: Response,
    authorization: str = AiosHeader(default=""),
    contextos_installation_id: str | None = Cookie(default=None),
):
    """
    Same as /license/purchase, but also generates and returns the 4 raw
    recovery codes - this is the ONLY response that will ever contain
    them. The frontend must show them immediately and never expect to
    fetch them again. Same anonymous-creation guard as /license/purchase.
    """
    db = None
    try:
        try:
            user_id = _require_user(authorization)
        except ValueError:
            user_id = None

        db = get_db_session()

        installation_id = None
        ip_hash = None
        if user_id is None:
            installation_id = (contextos_installation_id or "").strip()
            if not installation_id:
                import secrets
                installation_id = secrets.token_urlsafe(24)
                response.set_cookie(
                    key=INSTALLATION_ID_COOKIE,
                    value=installation_id,
                    httponly=True,
                    secure=True,
                    samesite="none",
                    max_age=60 * 60 * 24 * 365,
                )
            client_ip = request.client.host if request.client else None
            ip_hash = recovery_service.hash_ip(client_ip) if client_ip else None
            license_service.check_anonymous_creation_allowed(db, installation_id, ip_hash)

        license_result = license_service.create_license_after_payment(
            db, user_id, payload.plan, installation_id=installation_id, ip_hash=ip_hash
        )
        raw_codes = recovery_service.generate_recovery_codes(db, license_result["license_id"])
        return {"success": True, "license": license_result, "recovery_codes": raw_codes}
    except license_service.LicenseError as e:
        return {"success": False, "error": e.message}
    except Exception as e:
        print(f"[LICENSE] Unexpected error in /license/purchase-with-codes: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


class RecoverLicenseRequest(BaseModel):
    code: str


class RevealLicenseRequest(BaseModel):
    method: str  # "password" | "recovery_code"
    password: str | None = None
    recovery_code: str | None = None
    license_id: int | None = None  # required only for the anonymous (no-account) path


@app.post("/license/reveal")
def reveal_license(payload: RevealLicenseRequest, request: Request, authorization: str = AiosHeader(default="")):
    db = None
    try:
        db = get_db_session()
        client_ip = request.client.host if request.client else None
        ip_hash = recovery_service.hash_ip(client_ip)

        try:
            user_id = _require_user(authorization)
        except ValueError:
            user_id = None

        if user_id is not None:
            license = license_service.get_raw_license_for_user(db, user_id)

            if payload.method == "password":
                if not payload.password or not verify_user_password(db, user_id, payload.password):
                    return {"success": False, "error": "Incorrect password"}
            elif payload.method == "recovery_code":
                if not payload.recovery_code or not recovery_service.verify_code_ownership(
                    db, payload.recovery_code, license["license_id"], ip_hash
                ):
                    return {"success": False, "error": "Invalid recovery code"}
            else:
                return {"success": False, "error": "Invalid verification method"}

            db.add(SecurityEvent(event_type="LICENSE_KEY_REVEALED", user_id=user_id, success=True, ip_hash=ip_hash))
            db.commit()
            return {"success": True, "license_key": license["license_key"]}

        if not payload.license_id or not payload.recovery_code:
            return {"success": False, "error": "License ID and recovery code are required"}

        if not recovery_service.verify_code_ownership(db, payload.recovery_code, payload.license_id, ip_hash):
            return {"success": False, "error": "Invalid recovery code"}

        license_row = db.query(License).filter(License.id == payload.license_id).first()
        if not license_row:
            return {"success": False, "error": "License not found"}

        db.add(SecurityEvent(event_type="LICENSE_KEY_REVEALED", user_id=None, success=True, ip_hash=ip_hash))
        db.commit()
        return {"success": True, "license_key": license_row.license_key}
    except license_service.LicenseError as e:
        return {"success": False, "error": e.message}
    except Exception as e:
        print(f"[LICENSE] Unexpected error in /license/reveal: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.post("/license/recover")
def recover_license_route(payload: RecoverLicenseRequest, request: Request):
    db = None
    try:
        db = get_db_session()
        client_ip = request.client.host if request.client else None
        ip_hash = recovery_service.hash_ip(client_ip)
        result = recovery_service.recover_license(db, payload.code, ip_hash)
        return {"success": True, **result}
    except recovery_service.RecoveryError as e:
        return {"success": False, "error": e.message}
    except Exception as e:
        print(f"[RECOVERY] Unexpected error in /license/recover: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.post("/license/{license_id}/rotate-code")
def rotate_code_route(license_id: int, authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()

        owned = license_service.get_license_for_user(db, user_id)
        if owned["license_id"] != license_id:
            return {"success": False, "error": "License not found"}

        new_code = recovery_service.rotate_recovery_code(db, license_id)
        remaining = recovery_service.get_remaining_count(db, license_id)
        return {"success": True, "new_code": new_code, "recovery_codes_remaining": remaining}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except license_service.LicenseError as e:
        return {"success": False, "error": e.message}
    except Exception as e:
        print(f"[RECOVERY] Unexpected error in rotate-code: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


from services import aios_preferences_service, data_service


class AiosPreferencesRequest(BaseModel):
    personalization_level: str
    enabled_categories: list[str]


@app.get("/aios/preferences")
def get_aios_preferences(authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        result = aios_preferences_service.get_preferences(db, user_id)
        return {"success": True, **result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AIOS_PREFS] Unexpected error in GET /aios/preferences: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.post("/aios/preferences")
def post_aios_preferences(payload: AiosPreferencesRequest, authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        result = aios_preferences_service.update_preferences(
            db, user_id, payload.personalization_level, payload.enabled_categories
        )
        return {"success": True, **result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except aios_preferences_service.AiosPreferencesError as e:
        return {"success": False, "error": e.message}
    except Exception as e:
        print(f"[AIOS_PREFS] Unexpected error in POST /aios/preferences: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.post("/aios/reset-identity")
def reset_aios_identity_route(authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        aios_preferences_service.reset_aios_identity(db, user_id)
        return {"success": True}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AIOS_PREFS] Unexpected error in /aios/reset-identity: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.post("/data/clear-all")
def clear_all_data_route(authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        db = get_db_session()
        data_service.clear_all_data(db, user_id)
        return {"success": True}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[DATA] Unexpected error in /data/clear-all: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


class VerifyLicenseRequest(BaseModel):
    license_key: str


@app.post("/license/verify")
def verify_license_route(payload: VerifyLicenseRequest):
    """
    No-account path: look up a license by its key directly, no auth
    required - per the standalone license architecture.
    """
    db = None
    try:
        db = get_db_session()
        result = license_service.get_license_by_key(db, payload.license_key.strip())
        return {"success": True, "license": result}
    except license_service.LicenseError as e:
        return {"success": False, "error": e.message}
    except Exception as e:
        print(f"[LICENSE] Unexpected error in /license/verify: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


from services import free_license_service
from fastapi import Request as FreeLicenseRequest


class FreeLicenseInitRequest(BaseModel):
    pass


INSTALLATION_ID_COOKIE = "contextos_installation_id"


@app.post("/free-license/init")
def free_license_init(
    request: FreeLicenseRequest,
    response: Response,
    contextos_installation_id: str | None = Cookie(default=None),
):
    """
    Called once by the client (first launch, or whenever no local free
    license state exists) to create or fetch the server-side free-tier
    record. The installation identifier is server-generated and stored
    in an httpOnly cookie - never client-generated or JS-readable, so
    it can't be cleared via localStorage.clear() the way a client-side
    ID could. Safe to call repeatedly - returns the existing record if
    one is already there, applying the daily reset if a new day started.
    """
    inst_id = (contextos_installation_id or "").strip()
    is_new = not inst_id
    if is_new:
        import secrets
        inst_id = secrets.token_urlsafe(24)

    client_ip = request.client.host if request.client else None
    ip_hash = recovery_service.hash_ip(client_ip) if client_ip else None

    db = None
    try:
        db = get_db_session()
        result = free_license_service.get_or_create_free_license(db, inst_id, ip_hash)

        if is_new:
            response.set_cookie(
                key=INSTALLATION_ID_COOKIE,
                value=inst_id,
                httponly=True,
                secure=True,
                samesite="none",
                max_age=60 * 60 * 24 * 365,
            )

        return {"success": True, "license": result}
    except free_license_service.FreeLicenseError as e:
        return {"success": False, "error": e.message}
    except Exception as e:
        print(f"[FREE_LICENSE] Unexpected error in /free-license/init: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


ANON_COOKIE_NAME = "contextos_anon_id"


def _get_optional_user_id(authorization: str) -> int | None:
    try:
        return get_user_id_from_token(authorization)
    except AiosAuthError:
        return None


@app.get("/terms/status")
def terms_status(
    authorization: str = AiosHeader(default=""),
    contextos_anon_id: str | None = Cookie(default=None),
):
    db = None
    try:
        db = get_db_session()
        user_id = _get_optional_user_id(authorization)
        accepted = terms_service.has_accepted(db, user_id, contextos_anon_id)
        return {"success": True, "accepted": accepted}
    except Exception as e:
        print(f"[TERMS] Unexpected error in /terms/status: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.post("/terms/accept")
def terms_accept(
    response: Response,
    authorization: str = AiosHeader(default=""),
    contextos_anon_id: str | None = Cookie(default=None),
):
    db = None
    try:
        db = get_db_session()
        user_id = _get_optional_user_id(authorization)

        anon_id = contextos_anon_id
        if user_id is None and not anon_id:
            import secrets
            anon_id = secrets.token_urlsafe(24)
            response.set_cookie(
                key=ANON_COOKIE_NAME,
                value=anon_id,
                httponly=True,
                secure=True,
                samesite="none",
                max_age=60 * 60 * 24 * 365,
            )

        terms_service.accept(db, user_id, anon_id)
        return {"success": True}
    except Exception as e:
        print(f"[TERMS] Unexpected error in /terms/accept: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()
