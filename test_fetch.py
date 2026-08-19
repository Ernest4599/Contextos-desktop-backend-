import requests

url = "https://claude.ai/share/567d36e3-1810-416e-aa40-f8b0be6f35b2"

response = requests.get(url, headers={
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})

print("Status code:", response.status_code)
print("Content length:", len(response.text))
print("First 1000 chars:")
print(response.text[:1000])
