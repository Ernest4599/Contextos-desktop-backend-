p = "main.py"
s = open(p).read()

# --- 1. _pipeline_with_autosave signature: add is_metered/license_id params ---
old_sig = "async def _pipeline_with_autosave(messages, access: AccessContext, source: str):"
new_sig = "async def _pipeline_with_autosave(messages, access: AccessContext, source: str, is_metered: bool = False, license_id: int | None = None):"
assert s.count(old_sig) == 1, "signature anchor not found/not unique"
s = s.replace(old_sig, new_sig, 1)

# --- 2. add PRO commit/release block right before "yield chunk" ---
old_yield = "\n        yield chunk\n"
assert s.count(old_yield) == 1, "yield chunk anchor not found/not unique"
new_yield = '''
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
'''
s = s.replace(old_yield, new_yield, 1)

# --- 3. /import/share-link: was COMPLETELY unmetered - add free + pro gating ---
old_share = '''@app.post("/import/share-link")
async def import_share_link(payload: ShareLinkRequest, access: AccessContext = Depends(require_access)):
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
        }'''
assert s.count(old_share) == 1, "import_share_link anchor not found/not unique"
new_share = '''@app.post("/import/share-link")
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
        }'''
s = s.replace(old_share, new_share, 1)

# --- 4. /process/paste: add PRO branch, thread is_metered/license_id through ---
old_paste = '''    if access.via == "free":
        from services.db import get_db_session
        from services import free_license_service

        db = get_db_session()
        try:
            free_license_service.check_and_reserve_import(db, access.installation_id)
        except free_license_service.FreeLicenseError as e:
            return {"success": False, "error": e.message}
        finally:
            db.close()

    messages = split_messages(validated)
    return StreamingResponse(_pipeline_with_autosave(messages, access, source="import"), media_type="text/event-stream")'''
assert s.count(old_paste) == 1, "process_paste anchor not found/not unique"
new_paste = '''    is_metered = False
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
        from services import license_service

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
    return StreamingResponse(
        _pipeline_with_autosave(messages, access, source="import", is_metered=is_metered, license_id=access.license_id),
        media_type="text/event-stream",
    )'''
s = s.replace(old_paste, new_paste, 1)

open(p, "w").write(s)
print("main.py patched (signature, yield block, share-link, paste)")
