p = "main.py"
s = open(p).read()

old_upload = '''    if access.via == "free":
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


# TEMPORARY DEBUG'''
assert s.count(old_upload) == 1, "process_upload anchor not found/not unique"
new_upload = '''    is_metered = False
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

    return StreamingResponse(
        _pipeline_with_autosave(messages, access, source="import", is_metered=is_metered, license_id=access.license_id),
        media_type="text/event-stream",
    )


# TEMPORARY DEBUG'''
s = s.replace(old_upload, new_upload, 1)

open(p, "w").write(s)
print("main.py patched (process_upload)")
