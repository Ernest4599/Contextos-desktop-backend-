"""
Local smoke test for the /process/paste SSE endpoint. Run this against
your local server or the deployed Render URL to see the raw event
stream before wiring up the Node.js frontend.

Usage:
    python test_processing.py
    python test_processing.py https://contextos-desktop-backend.onrender.com
"""
import json
import sys

import requests

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

SAMPLE_CONVERSATION = """
User: I'm trying to build a mobile app that syncs notes across devices.
Right now the backend is only half-built - auth works but sync doesn't.
We decided to use Supabase instead of Firebase for the database.
I already set up the auth flow and wrote the login screen.
Still need to build the sync engine and add offline support.
I only have until Friday before my demo, so this has to move fast.
Not sure yet whether to use WebSockets or polling for sync.
By the way, the Supabase project ID is stored in .env as SUPABASE_URL.
Next, I need to start on the sync engine right now, that's the priority.
"""


def run():
    url = f"{BASE_URL}/process/paste"
    print(f"POST {url}\n")

    resp = requests.post(
        url,
        json={"text": SAMPLE_CONVERSATION},
        stream=True,
        timeout=120,
    )

    if resp.headers.get("content-type", "").startswith("application/json"):
        # Validation error path (paste_validator rejected it) - not a stream
        print("Non-streaming JSON response:")
        print(json.dumps(resp.json(), indent=2))
        return

    print(f"Status: {resp.status_code}")
    print("Streaming events:\n")

    event_name = None
    for raw_line in resp.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_str = line[len("data:"):].strip()
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                data = data_str
            print(f"[{event_name}] {json.dumps(data)}")

            if event_name == "complete":
                print("\n--- Context Package ---")
                print(data.get("context_package", ""))
            if event_name == "error":
                print(f"\n--- ERROR: {data.get('message')} ---")


if __name__ == "__main__":
    run()
