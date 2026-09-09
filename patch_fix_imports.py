p = "main.py"
s = open(p).read()

# Fix 1: add Form to the main fastapi import
old = '''from fastapi import FastAPI, UploadFile, File, Cookie, Response, Depends, Request'''
new = '''from fastapi import FastAPI, UploadFile, File, Cookie, Response, Depends, Request, Form'''
assert s.count(old) == 1, "fastapi import anchor not found/not unique"
s = s.replace(old, new, 1)

open(p, "w").write(s)
print("main.py: Form import fixed")
