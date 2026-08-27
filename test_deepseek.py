from curl_cffi import requests as crequests

r = crequests.get(
    "https://chat.deepseek.com/share/bcmi396ul1sg7ibplf",
    headers={"Accept": "text/html"},
    timeout=20,
    impersonate="chrome124",
)
print(r.status_code, len(r.text))
