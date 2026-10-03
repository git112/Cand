import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
path = ROOT / ".env"
print("Loading .env from:", path)
print("Exists:", path.exists())
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

print("\n--- ENV KEYS (masked) ---")
for k in ["GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_MODEL", "GEMINI_MODEL"]:
    v = os.environ.get(k, "")
    if len(v) > 12:
        print(f"  {k} = {v[:8]}...{v[-4:]} (len={len(v)})")
    else:
        print(f"  {k} = {repr(v)} (len={len(v)})")

sys.path.insert(0, str(ROOT))
from generate import available, provider_name, generate, _groq, _gemini

print("\n--- generate module state ---")
print("  available()      =", available())
print("  provider_name()  =", provider_name())

retrieved = [
    {
        "id": "TEST",
        "source_type": "meeting",
        "speaker_name": "Alex",
        "timestamp": "2026-09-15T10:00",
        "display_text": (
            "The onboarding v2 mockups were shared by Dana Lee in Figma on September 17. "
            "They are complete and posted at figma.example.com/file/onb-v2."
        ),
    }
]

print("\n--- Test Groq directly ---")
try:
    from generate import _prompt as _p
    prompt = _p("Are the onboarding mockups done?", "2026-09-18T18:00:00-07:00", retrieved)
    r = _groq(prompt)
    print("  _groq() result:", repr(r))
except Exception:
    print("  _groq() EXCEPTION:")
    traceback.print_exc()

print("\n--- Test Gemini directly ---")
try:
    prompt = _p("Are the onboarding mockups done?", "2026-09-18T18:00:00-07:00", retrieved)
    r = _gemini(prompt)
    print("  _gemini() result:", repr(r))
except Exception:
    print("  _gemini() EXCEPTION:")
    traceback.print_exc()

print("\n--- Test top-level generate() ---")
try:
    result = generate(
        "Are the onboarding mockups done?",
        "2026-09-18T18:00:00-07:00",
        retrieved,
    )
    print("  result:", repr(result))
except Exception:
    print("  EXCEPTION:")
    traceback.print_exc()
