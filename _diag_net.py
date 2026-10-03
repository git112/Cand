import os
import json
import traceback
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent
path = ROOT / ".env"
if path.exists():
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

def http_json(url, payload, headers, timeout=45):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                  headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read().decode("utf-8")), resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:600]
        return False, f"HTTP {e.code}: {body}", e.code
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", 0

GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
GEM_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")

print("=" * 60)
print("TEST 1a: Groq — model llama-3.3-70b-versatile (env-set)")
print("=" * 60)
ok, data, code = http_json(
    "https://api.groq.com/openai/v1/chat/completions",
    {
        "model": "llama-3.3-70b-versatile",
        "temperature": 0.1,
        "max_tokens": 80,
        "messages": [{"role": "user", "content": "Say hello in 3 words."}],
    },
    {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
)
print("ok=", ok, "code=", code)
if ok:
    print("RESP:", (data["choices"][0]["message"]["content"] or "")[:200])
else:
    print("ERR:", data)

print("\n" + "=" * 60)
print("TEST 1b: Groq — model llama-3.1-8b-instant (fallback)")
print("=" * 60)
ok, data, code = http_json(
    "https://api.groq.com/openai/v1/chat/completions",
    {
        "model": "llama-3.1-8b-instant",
        "temperature": 0.1,
        "max_tokens": 80,
        "messages": [{"role": "user", "content": "Say hello in 3 words."}],
    },
    {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
)
print("ok=", ok, "code=", code)
if ok:
    print("RESP:", (data["choices"][0]["message"]["content"] or "")[:200])
else:
    print("ERR:", data)

print("\n" + "=" * 60)
print("TEST 1c: Groq — model openai/gpt-oss-20b (OLD in generate.py)")
print("=" * 60)
ok, data, code = http_json(
    "https://api.groq.com/openai/v1/chat/completions",
    {
        "model": "openai/gpt-oss-20b",
        "temperature": 0.1,
        "max_tokens": 80,
        "messages": [{"role": "user", "content": "Say hello in 3 words."}],
    },
    {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
)
print("ok=", ok, "code=", code)
if ok:
    print("RESP:", (data["choices"][0]["message"]["content"] or "")[:200])
else:
    print("ERR:", data)

print("\n" + "=" * 60)
print("TEST 2: Gemini — gemini-2.0-flash")
print("=" * 60)
ok, data, code = http_json(
    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEM_KEY}",
    {
        "systemInstruction": {"parts": [{"text": "You are helpful."}]},
        "contents": [{"role": "user", "parts": [{"text": "Say hello in 3 words."}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 80},
    },
    {"Content-Type": "application/json"},
)
print("ok=", ok, "code=", code)
if ok:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        print("RESP:", text[:200])
    except Exception as e:
        print("PARSE ERR:", e, "| raw:", str(data)[:400])
else:
    print("ERR:", data)

print("\n" + "=" * 60)
print("TEST 3: Gemini — models.list (check API key validity)")
print("=" * 60)
try:
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={GEM_KEY}"
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        ls = json.loads(resp.read().decode("utf-8"))
        names = [m["name"].split("/")[-1] for m in ls.get("models", [])[:12]]
        print("OK, first 12 models:", names)
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode("utf-8", errors="replace")[:500])
except Exception as e:
    print(type(e).__name__, ":", e)
