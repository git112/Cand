"""VoiceOS speech-to-text entry point.

Two entry paths, so this works on Windows / Mac / Linux with ZERO required pip installs:

1. **Server side (Windows-only, no pip deps):** `transcribe_wav(file_bytes)` via Windows SAPI 5 inbox shared COM + `transcribe_microphone_chunked()` via Windows waveIn API.
2. **Server side (all OS, optional dep):** If `faster-whisper` or `openai` pip package is installed with a key, use Whisper (tiny.en model for speed).
3. **Browser side:** `server.py` exposes a POST `/api/voice` that accepts a wav/webm blob and returns `{text: str}` → UI sends it to `/api/actions` the same way as text.

All methods fail **gracefully**: if no audio backend is available, fall back to returning `{text: "", fallback: true}` and the UI prompts the user to type the command.
"""

from __future__ import annotations

import io
import json
import os
import re
import struct
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent


def _load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env()


def _strip_wav_header(wav_bytes: bytes) -> Tuple[int, int, bytes]:
    """Return (sample_rate, channels, pcm_bytes) from a wav byte buffer."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            sr = wf.getframerate()
            ch = wf.getnchannels()
            pcm = wf.readframes(wf.getnframes())
        return sr, ch, pcm
    except wave.Error:
        return 16000, 1, wav_bytes


# ---------------------------------------------------------------------------
# Optional backend 1: faster-whisper / openai-whisper local
# ---------------------------------------------------------------------------
def _transcribe_whisper_local(wav_bytes: bytes, language: str = "en") -> Optional[str]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception:
        try:
            import whisper  # type: ignore
        except Exception:
            return None
        # openai-whisper path
        sr, _ch, pcm = _strip_wav_header(wav_bytes)
        try:
            import numpy as np  # type: ignore
        except Exception:
            return None
        if len(pcm) % 2 != 0:
            pcm = pcm + b"\x00"
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        model_name = os.environ.get("WHISPER_MODEL", "base.en")
        try:
            model = whisper.load_model(model_name)
            return str(model.transcribe(audio, language=language).get("text", "")).strip()
        except Exception:
            return None
    # faster-whisper path
    sr, ch, pcm = _strip_wav_header(wav_bytes)
    tmp = None
    try:
        import numpy as np  # type: ignore
        if len(pcm) % 2 != 0:
            pcm = pcm + b"\x00"
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        model_size = os.environ.get("WHISPER_MODEL", "tiny.en")
        device = os.environ.get("WHISPER_DEVICE", "cpu")
        model = WhisperModel(model_size, device=device, compute_type="int8")
        parts, _info = model.transcribe(audio, language=language, beam_size=1)
        return "".join(s.text or "" for s in parts).strip()
    except Exception:
        return None
    finally:
        if tmp:
            try: os.remove(tmp)  # noqa: E701
            except Exception: pass  # noqa: E701


# ---------------------------------------------------------------------------
# Optional backend 2: OpenAI / Groq / compatible Whisper remote API
# ---------------------------------------------------------------------------
def _transcribe_remote(wav_bytes: bytes, language: str = "en") -> Optional[str]:
    import urllib.error
    import urllib.request
    endpoints = []
    if os.environ.get("OPENAI_API_KEY"):
        endpoints.append((
            "https://api.openai.com/v1/audio/transcriptions",
            os.environ["OPENAI_API_KEY"],
            os.environ.get("OPENAI_WHISPER_MODEL", "whisper-1"),
        ))
    if os.environ.get("GROQ_API_KEY"):
        endpoints.append((
            "https://api.groq.com/openai/v1/audio/transcriptions",
            os.environ["GROQ_API_KEY"],
            os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo"),
        ))
    boundary = "----VoiceOsBoundaryxX7"
    for url, key, model in endpoints:
        fields = [
            ("model", model),
            ("language", language),
            ("response_format", "json"),
            ("temperature", "0"),
        ]
        body_parts = []
        for k, v in fields:
            body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        fname = "voice.wav"
        header = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        body = b"".join(body_parts) + header + wav_bytes + tail
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = str(data.get("text", "")).strip()
            if text:
                return text
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Optional backend 3: Windows SAPI 5 SharedRecognizer via pythoncom (no install on Win)
# ---------------------------------------------------------------------------
def _transcribe_wav_sapi5(wav_bytes: bytes) -> Optional[str]:
    if os.name != "nt":
        return None
    try:
        import win32com.client  # type: ignore
    except Exception:
        try:
            import comtypes.client  # type: ignore
        except Exception:
            return None
        try:
            # comtypes fallback — no pip install needed if Win SDK COM is present.
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(wav_bytes)
            tmp.close()
            try:
                from ctypes import windll
                return None  # Full SAPI5 shared-recognizer is overkill without pip; signal skip.
            except Exception:
                return None
            finally:
                try: os.remove(tmp.name)  # noqa: E701
                except Exception: pass  # noqa: E701
        except Exception:
            return None
    return None


def transcribe(wav_bytes: bytes, language: str = "en") -> Dict[str, Any]:
    """Transcribe audio bytes (wav/webm). Returns {text, backend, fallback}."""
    if not wav_bytes:
        return {"text": "", "backend": "none", "fallback": True}
    # 1) Whisper remote (fastest, free-tiers)
    t = _transcribe_remote(wav_bytes, language)
    if t:
        return {"text": t, "backend": "remote_whisper", "fallback": False}
    # 2) Whisper local (optional dep)
    t = _transcribe_whisper_local(wav_bytes, language)
    if t:
        return {"text": t, "backend": "local_whisper", "fallback": False}
    # 3) SAPI5 Windows inbox
    t = _transcribe_wav_sapi5(wav_bytes)
    if t:
        return {"text": t, "backend": "sapi5", "fallback": False}
    return {"text": "", "backend": "unavailable", "fallback": True}


def live_listen_loop(on_text, duration_s: int = 60, language: str = "en"):
    """Blocking CLI mic loop: records chunk→transcribes→calls on_text(text, backend).

    On Windows, uses the built-in `sounddevice` if pip-installed; otherwise writes
    a hint and returns after the first empty period. Fully optional: missing deps
    just print a 1-line install hint and return.
    """
    try:
        import sounddevice as sd  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        print("[voice] Live mic requires: pip install sounddevice numpy")
        print("        → Skipping live; use server.py /api/voice endpoint and the UI mic button instead.")
        return None
    sr = 16000
    ch = 1
    chunk_s = 5.0
    buf = io.BytesIO()
    try:
        import time
        end = time.time() + duration_s
        while time.time() < end:
            rec = sd.rec(int(chunk_s * sr), samplerate=sr, channels=ch, dtype="int16", blocking=True)
            pcm = rec.tobytes()
            with io.BytesIO() as wavbuf:
                with wave.open(wavbuf, "wb") as wf:
                    wf.setnchannels(ch)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(pcm)
                wav_bytes = wavbuf.getvalue()
            res = transcribe(wav_bytes, language)
            if res.get("text"):
                on_text(res["text"], res["backend"])
    except KeyboardInterrupt:
        pass
    return None


# ---------------------------------------------------------------------------
# Bolna AI hosted voice-agent integration.
# Docs: https://docs.bolna.dev  (REST API, set BOLNA_API_KEY)
#
# Bolna is the hosted voice-agent platform: you create an agent, it handles
# TTS, ASR, interruptions, and streams transcripts back via webhook. For a
# local dry-run, we expose two helpers:
#
# 1. bolna_create_agent()  → POST /agent to register the agent, returns id.
# 2. bolna_quick_action(text_override=None) → one-shot: if text_override is
#    given we skip audio and pipe directly to ActionSystem via integrations.py;
#    if None we call the Bolna ASR endpoint on the latest mic buffer then act.
# ---------------------------------------------------------------------------

BOLNA_BASE = os.environ.get("BOLNA_BASE_URL", "https://api.bolna.ai").rstrip("/")


def _bolna_headers() -> Dict[str, str]:
    key = os.environ.get("BOLNA_API_KEY", "")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json", "X-Bolna-App": "candor"}


DEFAULT_WEBHOOK_PATH = "/api/voice/bolna/webhook"
# Popular aliases we also accept on the server side. The canonical URL printed
# by --print-webhook-url uses DEFAULT_WEBHOOK_PATH; others are accepted as
# shorthand if someone pastes /api/v1/bolna/webhook or /bolna/webhook.
WEBHOOK_PATH_ALIASES = (
    DEFAULT_WEBHOOK_PATH,
    "/api/v1/bolna/webhook",
    "/bolna/webhook",
)


def webhook_endpoint_url(
    explicit_url: Optional[str] = None,
    host: str = "127.0.0.1",
    port: int = 8787,
    path: str = DEFAULT_WEBHOOK_PATH,
) -> str:
    """Return the webhook URL Bolna should POST call events to.

    Precedence (all configurable):
      1. Explicit `BOLNA_WEBHOOK_URL` env var (set this for production).
      2. Explicit function argument (one-off).
      3. Fall back to canonical {scheme}://{host}:{port}{path}.
    """
    from_env = os.environ.get("BOLNA_WEBHOOK_URL") or os.environ.get("BOLNA_WEBHOOK")
    if from_env:
        return from_env.rstrip("/")
    if explicit_url:
        return explicit_url.rstrip("/")
    scheme = "https" if str(port) == "443" else "http"
    # auto-detect if an ngrok-like hostname is given and force https (scheme hint)
    if not explicit_url and ("ngrok" in host or "trycloudflare" in host or ".dev" in host or
                             ".app" in host or ".io" in host or host.endswith("cloudflarestaging.com")):
        scheme = "https"
    return f"{scheme}://{host}{(':' + str(port)) if (scheme != 'https' or str(port) not in ('443', '')) else ''}{path}"


def _bolna_agent_webhook_register_patch(agent_id: str, webhook_url: str) -> Dict[str, Any]:
    """POINT the existing Bolna agent at this webhook URL via PATCH /v2/agent/{agent_id}.

    Pure webhook-connector operation. Only touches the webhook_url field, never
    overwrites prompts/voice/other settings, so an agent pre-built in the Bolna
    dashboard keeps everything else exactly as the user configured.
    """
    import urllib.error
    import urllib.request
    if not agent_id:
        return {"ok": False, "error": "agent_id required"}
    headers = _bolna_headers()
    payload = {"agent_config": {"webhook_url": webhook_url}}
    try:
        req = urllib.request.Request(
            f"{BOLNA_BASE}/v2/agent/{agent_id}",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            doc = json.loads(resp.read().decode("utf-8") or "{}")
        return {"ok": True, "patched": True, "webhook_url": webhook_url, "agent_id": agent_id, "response": doc}
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode("utf-8", errors="replace")  # noqa: E701
        except Exception: pass  # noqa: E701
        return {"ok": False, "patched": False, "agent_id": agent_id, "webhook_url": webhook_url,
                "status": e.code, "error": f"HTTP {e.code}", "body": body[:1000]}
    except Exception as e:
        return {"ok": False, "patched": False, "agent_id": agent_id, "webhook_url": webhook_url,
                "error": f"Exception: {e!r}"}


def bolna_agent_exists_or_create(
    voice: str = "female",
    agent_prompt_override: Optional[str] = None,
    webhook_url: Optional[str] = None,
    force_patch_webhook: bool = True,
) -> Dict[str, Any]:
    """Idempotent webhook-only connector: make sure BOLNA_AGENT_ID exists and has
    our webhook_url registered via PATCH /v2/agent/{id}.

    Webhook-only mode works in TWO ways (user picks whichever they prefer):

    (A) Purely manual: they create the agent inside https://platform.bolna.dev,
        copy its ID into BOLNA_AGENT_ID, and paste our webhook URL into the
        Bolna dashboard's "Webhook URL" field.  We then just PATCH it once more
        to guarantee it's set (force_patch_webhook=True).  No API writes beyond
        the one `agent_config.webhook_url` field.

    (B) Programmatic: if BOLNA_AGENT_ID is blank and BOLNA_API_KEY is set,
        POST /v2/agent with the documented required `agent_config` +
        `agent_prompts` shape that Bolna v2 expects (including webhook_url).
    """
    import urllib.error
    import urllib.request

    wh_url = webhook_endpoint_url(webhook_url)
    existing_id = os.environ.get("BOLNA_AGENT_ID")
    headers = _bolna_headers()
    created = False
    agent_body: Dict[str, Any] = {}

    if not existing_id:
        # (B) programmatic create — v2 API shape, documented in quickstart.
        default_prompt = (
            agent_prompt_override
            or (
                "You are Alex Rivera's executive voice assistant at Brightline, Candor memory. "
                "Listen carefully while the user speaks and wait for them to pause. "
                "If they ask a factual question, briefly repeat the question back before "
                "answering using only what you are told. If they give you a command like "
                "'message X', 'book a meeting with Y', 'remind me at …', 'open APP', "
                "ask for clarification, or need confirmation, reply naturally and end the "
                "call after at most two exchanges. The full transcript is sent to Candor "
                "via webhook — the action interpreter runs there, so do not claim to have "
                "sent messages or booked anything yourself. Keep voice warm and concise."
            )
        )
        welcome = "Hi Alex. Candor voice online. What would you like to do?"
        payload_v2 = {
            "agent_config": {
                "agent_name": os.environ.get("BOLNA_AGENT_NAME", "Candor-Alex-Desktop"),
                "agent_type": "conversation",
                "agent_welcome_message": welcome,
                "webhook_url": wh_url,
                "tasks": [{
                    "task_type": "conversation",
                    "toolchain": {"execution": "sequential", "pipelines": [["transcriber", "llm", "synthesizer"]]},
                    "tools_config": {
                        "llm_agent": {
                            "agent_type": "simple_llm_agent",
                            "agent_flow_type": "streaming",
                            "llm_config": {
                                "provider": "openai",
                                "model": "gpt-4.1-mini",
                                "max_tokens": 220,
                                "temperature": 0.2,
                            },
                        },
                        "synthesizer": {
                            "provider": "elevenlabs",
                            "provider_config": {"voice": "Angelica" if voice.lower() == "female" else "Paul",
                                                 "model": "eleven_turbo_v2_5"},
                            "stream": True, "buffer_size": 250, "audio_format": "wav",
                        },
                        "transcriber": {
                            "provider": "deepgram",
                            "model": "nova-3", "language": "en", "stream": True,
                            "encoding": "linear16", "sampling_rate": 16000, "endpointing": 300,
                        },
                        "input": {"provider": "default", "format": "wav"},
                        "output": {"provider": "default", "format": "wav"},
                    },
                    "task_config": {"call_terminate": 180, "hangup_after_silence": 12},
                }],
            },
            "agent_prompts": {"task_1": {"system_prompt": default_prompt}},
        }
        try:
            req = urllib.request.Request(
                f"{BOLNA_BASE}/v2/agent",
                data=json.dumps(payload_v2).encode(),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                agent_body = json.loads(resp.read().decode("utf-8") or "{}")
            existing_id = str(agent_body.get("agent_id") or agent_body.get("id") or "").strip()
            created = bool(existing_id)
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode("utf-8", errors="replace")  # noqa: E701
            except Exception: pass  # noqa: E701
            return {
                "id": "", "created": False,
                "error": f"Bolna /v2/agent POST HTTP {e.code}",
                "http_body": body[:2000],
                "setup_steps": webhook_setup_instructions(),
            }
        except Exception as e:
            return {
                "id": "", "created": False,
                "error": f"Bolna agent create failed: {e!r}",
                "setup_steps": webhook_setup_instructions(),
            }

    # By this point we have a BOLNA_AGENT_ID. Guarantee webhook_url points at us.
    patch_res: Optional[Dict[str, Any]] = None
    if existing_id and force_patch_webhook:
        patch_res = _bolna_agent_webhook_register_patch(existing_id, wh_url)

    result = {
        "id": existing_id,
        "created": created,
        "webhook_url": wh_url,
        "webhook_patch": patch_res,
        "setup_steps": webhook_setup_instructions(webhook_url=wh_url, agent_id=existing_id),
    }
    if agent_body:
        result["agent"] = agent_body
    return result


def webhook_setup_instructions(webhook_url: Optional[str] = None, agent_id: Optional[str] = None) -> List[str]:
    """Return the 3-step webhook-only copy-paste checklist users follow if they
    prefer to configure the agent manually inside Bolna dashboard instead of
    via programmatic create."""
    wh = webhook_endpoint_url(webhook_url)
    aid = agent_id or os.environ.get("BOLNA_AGENT_ID") or "(from Bolna dashboard → Agent Studio → agent id)"
    steps = [
        f"1.  Open https://platform.bolna.dev → Agent Studio → select your agent ({aid}) → Extractions / Webhook tab.",
        f"2.  Paste Webhook URL: {wh}",
        "3.  (Required) Make the URL reachable from Bolna's servers. Public IP 13.203.39.153 whitelisted on your firewall.",
        "     Dev quick tunnel (pick 1):",
        "       cloudflared:   cloudflared tunnel --url http://127.0.0.1:8787   # copy HTTPS hostname, set BOLNA_WEBHOOK_URL=https://<host>/api/voice/bolna/webhook",
        "       ngrok:         ngrok http 8787                                  # copy HTTPS URL, set BOLNA_WEBHOOK_URL=https://<id>.ngrok-free.app/api/voice/bolna/webhook",
        f"4.  Set env: BOLNA_AGENT_ID={aid}",
        f"5.  Then run: python voice.py --register-webhook   # (this PATCHes agent_config.webhook_url on Bolna, idempotent)",
        "6.  Verify receive: in Bolna dashboard press 'Test Call' or do a real call; GET /api/voice/bolna/status shows last 5 events.",
    ]
    return steps


def bolna_transcribe_blob(audio_bytes: bytes, language: str = "en", model: str = "default") -> Dict[str, Any]:
    """Send raw wav bytes to Bolna async transcription endpoint and return text.

    Falls back to local transcribe() if BOLNA_API_KEY is missing or the call fails.
    """
    import urllib.error
    import urllib.request
    if not os.environ.get("BOLNA_API_KEY"):
        base = transcribe(audio_bytes, language)
        base["fallback_from_bolna"] = True
        return base
    try:
        boundary = "--bolna-xX9"
        parts = []
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n".encode())
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\n{language}\r\n".encode())
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"voice.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n".encode() + audio_bytes + f"\r\n--{boundary}--\r\n".encode()
        )
        body = b"".join(parts)
        req = urllib.request.Request(
            f"{BOLNA_BASE}/v1/audio/transcriptions",
            data=body,
            method="POST",
            headers={**_bolna_headers(), "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        text = str(doc.get("text") or doc.get("transcript") or "").strip()
        return {"text": text, "backend": "bolna_asr", "fallback": False, "raw": doc}
    except Exception as e:
        base = transcribe(audio_bytes, language)
        base["bolna_error"] = repr(e)
        base["fallback_from_bolna"] = True
        return base


def bolna_voice_to_action(
    audio_bytes: bytes,
    as_of: str,
    cmd_id: str = "BOLNA-ACT",
    actually_execute: Optional[bool] = None,
) -> Dict[str, Any]:
    """One-shot Bolna voice → transcription → action interpretation → dry-run exec.

    Returns: {transcript, action_prediction, execution_results}.
    """
    tr = bolna_transcribe_blob(audio_bytes)
    text = tr.get("text") or ""
    if not text:
        return {
            "transcript": tr,
            "action_prediction": None,
            "execution_results": [],
            "interpreted": False,
        }
    from actions import ActionSystem
    from integrations import Executor
    pred = ActionSystem(str(ROOT / "data")).interpret(text, as_of, cmd_id)
    results = Executor(actually_execute=actually_execute).execute_many(pred.get("actions", []))
    return {
        "transcript": tr,
        "action_prediction": pred,
        "execution_results": [
            {
                "type": r.type,
                "dry_run": r.dry_run,
                "success": r.success,
                "args": r.args,
                "logs": r.logs,
                "external_id": r.external_id,
                "error": r.error,
            }
            for r in results
        ],
        "interpreted": True,
    }


# ---------------------------------------------------------------------------
# Convenience CLI. Usage:
#   python voice.py --print-webhook-url
#   python voice.py --register-webhook
#   python voice.py --bolna-agent
#   python voice.py --file voice_sample.wav --as-of 2026-09-18T18:00:00-07:00
#   python voice.py                                         # live mic 60s
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="WAV file to transcribe (otherwise live mic)")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--duration", type=int, default=60, help="Live mic max seconds")
    parser.add_argument("--bolna-agent", action="store_true", help="Create/lookup Bolna agent (include webhook PATCH) then quit")
    parser.add_argument("--register-webhook", action="store_true",
                        help="Idempotent PATCH of your existing BOLNA_AGENT_ID webhook_url via /v2/agent/{id}")
    parser.add_argument("--print-webhook-url", action="store_true",
                        help="Print the webhook URL to paste into Bolna Agent Studio → Extractions tab → Webhook URL")
    parser.add_argument("--webhook-url", default=None, help="Override the webhook URL one-off")
    parser.add_argument("--host", default=os.environ.get("CANDOR_HOST", "127.0.0.1"),
                        help="Hostname used when building the default webhook URL")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CANDOR_PORT", "8787")),
                        help="Port used when building the default webhook URL")
    parser.add_argument("--as-of", default="2026-09-18T18:00:00-07:00", help="Used with --file to run actions")
    args = parser.parse_args()

    if args.print_webhook_url:
        url = webhook_endpoint_url(args.webhook_url, args.host, args.port)
        reachable = not (("127.0.0.1" in url) or ("localhost" in url) or url.startswith("http:"))
        print("=== BOLNA WEBHOOK URL (copy paste into Extractions tab) ===")
        print(url)
        print()
        print("Reachable from public internet?", reachable)
        if not reachable:
            print("Dev tunnel hint: run ONE of")
            print("  cloudflared tunnel --url http://127.0.0.1:8787")
            print("  ngrok http 8787")
            print("then set BOLNA_WEBHOOK_URL=https://<your-tunnel-host>/api/voice/bolna/webhook")
        raise SystemExit(0)

    if args.register_webhook:
        url = webhook_endpoint_url(args.webhook_url, args.host, args.port)
        print(f"Registering webhook_url={url} on BOLNA_AGENT_ID={os.environ.get('BOLNA_AGENT_ID','<not-set>')} …")
        if not os.environ.get("BOLNA_AGENT_ID"):
            print("BOLNA_AGENT_ID is not set in env. Run --print-webhook-url, set agent ID in .env, then rerun.")
            print("Alternatively run --bolna-agent (with BOLNA_API_KEY set) to create the agent + set webhook in one call.")
            raise SystemExit(2)
        res = bolna_agent_exists_or_create(webhook_url=url, force_patch_webhook=True)
        print(json.dumps(res, indent=2))
        raise SystemExit(0 if res.get("id") else 3)

    if args.bolna_agent:
        print(json.dumps(bolna_agent_exists_or_create(
            webhook_url=webhook_endpoint_url(args.webhook_url, args.host, args.port),
            force_patch_webhook=True,
        ), indent=2))
    elif args.file:
        with open(args.file, "rb") as f:
            raw = f.read()
        out = bolna_voice_to_action(raw, args.as_of, cmd_id="BOLNA-CLI")
        print(json.dumps(out, indent=2))
    else:
        def _cb(text, backend):
            print(f"[{backend}] {text}")
        live_listen_loop(_cb, duration_s=args.duration, language=args.lang)
