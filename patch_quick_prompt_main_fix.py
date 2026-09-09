p = "main.py"
s = open(p).read()

old = '''        allowed_providers = ["gemini"] if access.via == "free" else license_service.PLAN_PROVIDERS.get(access.plan)
        result = generate_quick_prompt(payload.overview, payload.decisions, payload.task, allowed_providers=allowed_providers)'''

new = '''        allowed_providers = ["gemini"] if access.via == "free" else license_service.PLAN_PROVIDERS.get(access.plan)

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

        result = generate_quick_prompt(
            payload.overview, payload.decisions, payload.task,
            allowed_providers=allowed_providers, aios_context=aios_context,
        )'''

assert s.count(old) == 1, "main.py: quick_prompt generation call anchor not found/not unique"
s = s.replace(old, new, 1)

open(p, "w").write(s)
print("main.py patched")
