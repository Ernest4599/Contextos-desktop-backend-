p = "main.py"
s = open(p).read()

old = '''    allowed_providers = license_service.PLAN_PROVIDERS.get(access.plan)'''
new = '''    allowed_providers = ["gemini"] if access.via == "free" else license_service.PLAN_PROVIDERS.get(access.plan)'''
count = s.count(old)
assert count == 3, f"expected 3 occurrences (share-link, paste, upload), found {count}"
s = s.replace(old, new)

open(p, "w").write(s)
print(f"main.py patched ({count} call sites: share-link, paste, upload)")
