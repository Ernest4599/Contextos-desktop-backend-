p = "main.py"
s = open(p).read()

# 1. QuickPromptRequest: add clarifications field
old_req = '''class QuickPromptRequest(BaseModel):
    overview: str = ""
    decisions: str = ""
    task: str = ""
    project_id: int | None = None'''
new_req = '''class ClarificationItem(BaseModel):
    question: str
    answer: str


class QuickPromptRequest(BaseModel):
    overview: str = ""
    decisions: str = ""
    task: str = ""
    project_id: int | None = None
    clarifications: list[ClarificationItem] = []'''
assert s.count(old_req) == 1, "QuickPromptRequest anchor not found/not unique"
s = s.replace(old_req, new_req, 1)

# 2. Full route body replacement
old_route = '''@app.post("/quick-prompt")
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

        allowed_providers = ["gemini"] if access.via == "free" else license_service.PLAN_PROVIDERS.get(access.plan)

        aios_context = None
        if access.via == "session" and access.plan not in (None, "free"):
            from services import aios_service
            aios_db = get_db_session()
            try:
                aios_context = aios_service.get_context_for_quick_prompt(aios_db, access.user_id)
            except Exception as e:
                print(f"[QUICK_PROMPT] Failed to fetch AIOS context, continuing without it: {e}")
                aios_context = None
            finally:
                aios_db.close()

        project_context = None
        if access.via == "session" and access.user_id and payload.project_id:
            proj_db = get_db_session()
            try:
                project_packages = package_service.list_packages_for_project(proj_db, access.user_id, payload.project_id)
                if project_packages:
                    project_context = [p["content"] for p in project_packages]
            except Exception as e:
                print(f"[QUICK_PROMPT] Failed to fetch project context, continuing without it: {e}")
                project_context = None
            finally:
                proj_db.close()

        result = generate_quick_prompt(
            payload.overview, payload.decisions, payload.task,
            allowed_providers=allowed_providers, aios_context=aios_context, project_context=project_context,
        )

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
                    db, access.user_id, source="quick_prompt", title=title, content=result["prompt"], project_id=payload.project_id
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
                release_db.close()'''

new_route = '''@app.post("/quick-prompt")
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
    # Clarification-eligible plans peek-check credits instead of reserving
    # upfront, since asking a question costs nothing - only a completed
    # generation is charged, and only after we know it succeeded.
    clarification_eligible = (
        is_metered
        and access.via == "session"
        and access.plan in license_service.CLARIFICATION_ELIGIBLE_PLANS
    )
    reserved = False

    try:
        if is_metered and not clarification_eligible:
            credit_db = get_db_session()
            try:
                license_service.check_and_reserve_credits(credit_db, access.license_id)
                reserved = True
            except license_service.LicenseError as e:
                return {"success": False, "error": e.message}
            finally:
                credit_db.close()
        elif clarification_eligible:
            peek_db = get_db_session()
            try:
                if not license_service.has_sufficient_credits(peek_db, access.license_id):
                    return {"success": False, "error": "You've reached your usage limit."}
            finally:
                peek_db.close()

        allowed_providers = ["gemini"] if access.via == "free" else license_service.PLAN_PROVIDERS.get(access.plan)

        aios_context = None
        if access.via == "session" and access.plan not in (None, "free"):
            from services import aios_service
            aios_db = get_db_session()
            try:
                aios_context = aios_service.get_context_for_quick_prompt(aios_db, access.user_id)
            except Exception as e:
                print(f"[QUICK_PROMPT] Failed to fetch AIOS context, continuing without it: {e}")
                aios_context = None
            finally:
                aios_db.close()

        project_context = None
        if access.via == "session" and access.user_id and payload.project_id:
            proj_db = get_db_session()
            try:
                project_packages = package_service.list_packages_for_project(proj_db, access.user_id, payload.project_id)
                if project_packages:
                    project_context = [p["content"] for p in project_packages]
            except Exception as e:
                print(f"[QUICK_PROMPT] Failed to fetch project context, continuing without it: {e}")
                project_context = None
            finally:
                proj_db.close()

        clarifications_list = [{"question": c.question, "answer": c.answer} for c in payload.clarifications]

        result = generate_quick_prompt(
            payload.overview, payload.decisions, payload.task,
            allowed_providers=allowed_providers, aios_context=aios_context, project_context=project_context,
            allow_clarification=clarification_eligible, clarifications=clarifications_list,
        )

        if result.get("needs_clarification"):
            # Free - no reservation was made (non-eligible plans can never
            # reach this branch, since allow_clarification is False for them).
            return {"success": True, **result}

        if is_metered and not clarification_eligible:
            commit_db = get_db_session()
            try:
                license_service.commit_credits(commit_db, access.license_id)
            finally:
                commit_db.close()
        elif clarification_eligible:
            # Full prompt confirmed - charge now, post-hoc.
            charge_db = get_db_session()
            try:
                license_service.check_and_reserve_credits(charge_db, access.license_id)
            except license_service.LicenseError as e:
                return {"success": False, "error": e.message}
            finally:
                charge_db.close()

        if access.via == "session" and access.user_id and result.get("prompt"):
            db = None
            try:
                db = get_db_session()
                title = (payload.task or "Quick Prompt").strip() or "Quick Prompt"
                package_service.save_package(
                    db, access.user_id, source="quick_prompt", title=title, content=result["prompt"], project_id=payload.project_id
                )
            except Exception as e:
                print(f"[PACKAGES] Failed to auto-save quick-prompt package: {e}")
            finally:
                if db is not None:
                    db.close()

        return {"success": True, **result}
    except QuickPromptValidationError as e:
        if is_metered and not clarification_eligible and reserved:
            release_db = get_db_session()
            try:
                license_service.release_credits(release_db, access.license_id)
            finally:
                release_db.close()
        return {"success": False, "error": e.message}
    except QuickPromptError as e:
        if is_metered and not clarification_eligible and reserved:
            release_db = get_db_session()
            try:
                license_service.release_credits(release_db, access.license_id)
            finally:
                release_db.close()
        return {"success": False, "error": str(e)}
    except Exception as e:
        if is_metered and not clarification_eligible and reserved:
            release_db = get_db_session()
            try:
                license_service.release_credits(release_db, access.license_id)
            finally:
                release_db.close()'''

assert s.count(old_route) == 1, "quick_prompt route anchor not found/not unique"
s = s.replace(old_route, new_route, 1)

open(p, "w").write(s)
print("main.py patched")
