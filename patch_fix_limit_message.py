p = "services/license_service.py"
s = open(p).read()
old = '''        raise LicenseError("You've reached your Pro usage limit.")'''
new = '''        raise LicenseError("You've reached your usage limit.")'''
assert s.count(old) == 1, "anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("license_service.py patched")
