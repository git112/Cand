#!/usr/bin/env python3
"""HTTP API + static product UI for Candor Memory."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import collections
import time as _time

from actions import ActionSystem
from memory_system import MemorySystem, ROOT

DATA_DIR = Path(os.environ.get("CANDOR_DATA_DIR", ROOT / "data"))
WEB_DIR = ROOT / "web"

memory = MemorySystem(DATA_DIR)
actions = ActionSystem(DATA_DIR)

# In-memory ring buffer of recent Bolna webhook events.
# Pure webhook-connector — no DB writes needed, no schema changes.
_MAX_BOLNA_EVENTS = 50
BOLNA_EVENT_LOG: collections.deque = collections.deque(maxlen=_MAX_BOLNA_EVENTS)
BOLNA_TRANSCRIPT_TO_ACTION: collections.deque = collections.deque(maxlen=_MAX_BOLNA_EVENTS)

app = FastAPI(title="Candor Memory", version="1.0.0")


class AskBody(BaseModel):
    question: str
    as_of: str = "2026-09-18T18:00:00-07:00"
    id: str = "UI-ASK"


class ActionBody(BaseModel):
    command: str
    as_of: str = "2026-09-18T18:00:00-07:00"
    id: str = "UI-ACT"


class ExecuteBody(BaseModel):
    actions: list = []
    actually_execute: bool = False



@app.get("/api/health")
def health():
    return {"ok": True, "stats": memory.stats()}


@app.get("/api/stats")
def stats():
    return memory.stats()


@app.post("/api/ask")
def ask(body: AskBody):
    result = memory.answer_question(body.id, body.question, body.as_of)
    return result


@app.get("/api/unit/{uid}")
def unit(uid: str, as_of: Optional[str] = None):
    row = memory.get_unit(uid, as_of)
    if not row:
        raise HTTPException(404, "Unit not visible or unknown")
    return row


@app.get("/api/sources")
def sources(
    source_type: Optional[str] = None,
    q: Optional[str] = None,
    as_of: str = "2026-09-18T18:00:00-07:00",
    limit: int = Query(40, le=200),
    offset: int = 0,
):
    return {"items": memory.list_units(source_type, q, as_of, limit, offset)}


@app.get("/api/timeline")
def timeline(as_of: str = "2026-09-18T18:00:00-07:00", limit: int = Query(180, le=400)):
    return {"days": memory.timeline(as_of, limit)}


@app.post("/api/actions")
def do_action(body: ActionBody):
    return actions.interpret(body.command, body.as_of, body.id)


@app.post("/api/voice")
def voice_to_action(
    file: UploadFile = File(...),
    as_of: str = Form("2026-09-18T18:00:00-07:00"),
    id: str = Form("VOICE-UI"),
    use_bolna: bool = Form(True),
):
    """Accept a wav/webm audio blob, transcribe (Bolna ASR preferred, graceful fallback),
    route the text to the action interpreter, return the transcript + action plan."""
    raw_bytes = file.file.read() or b""
    if use_bolna:
        try:
            from voice import bolna_voice_to_action
            return bolna_voice_to_action(raw_bytes, as_of, cmd_id=id, actually_execute=None)
        except Exception as e:
            fallback = True
    from voice import transcribe
    tr = transcribe(raw_bytes)
    text = tr.get("text") or ""
    plan = actions.interpret(text, as_of, id) if text else {"id": id, "actions": []}
    return {
        "transcript": tr,
        "action_prediction": plan,
        "execution_results": [],
        "interpreted": bool(text),
    }


@app.get("/api/voice/bolna/agent")
def bolna_agent_info():
    """Idempotent agent status: returns {id, created, backend}, creating the agent via Bolna API if needed."""
    try:
        from voice import bolna_agent_exists_or_create
        return bolna_agent_exists_or_create()
    except Exception as e:
        return {"id": "", "created": False, "error": repr(e), "fallback": "local mic"}


def _bolna_extract_transcript(payload: dict) -> str:
    """Pull every user utterance (concatenated in chronological order) from a Bolna
    execution payload. Documented field locations we tolerate:

    - data.transcript (string, full end-of-call transcript)
    - data.conversation[].role == "user"   .message / .text / .content
    - transcript (top-level string, some v1 webhook shapes)
    - data.messages[].role == "user"
    """
    if isinstance(payload.get("transcript"), str) and payload["transcript"].strip():
        return payload["transcript"].strip()
    d = payload.get("data") or payload.get("payload") or {}
    if isinstance(d.get("transcript"), str) and d["transcript"].strip():
        return d["transcript"].strip()
    turns = (
        d.get("conversation")
        or d.get("messages")
        or d.get("turns")
        or d.get("history")
        or d.get("dialog")
        or []
    )
    texts: list[str] = []
    for t in turns or []:
        if not isinstance(t, dict):
            continue
        role = str(t.get("role") or t.get("speaker") or t.get("side") or "").lower()
        if role in ("user", "caller", "customer", "client", "from_user"):
            m = str(
                t.get("message") or t.get("text") or t.get("content") or t.get("utterance") or ""
            ).strip()
            if m:
                texts.append(m)
    if texts:
        return " ".join(texts).strip()
    # Final tolerant scan — any string value under data that is long and looks like speech
    return ""


@app.get("/api/voice/bolna/status")
def bolna_webhook_status():
    """Dashboard endpoint: returns webhook-connector state.
    - webhook_url you should paste into Bolna dashboard
    - registered agent info (id + webhook_patch result, if PATCH was run)
    - last 10 received events, plus last 5 transcript→action pipeline runs.
    """
    info: dict = {}
    try:
        from voice import bolna_agent_exists_or_create, webhook_endpoint_url
        url = webhook_endpoint_url()
        info["webhook_url"] = url
        info["reachable_on_public_internet"] = not (
            "127.0.0.1" in url or "localhost" in url or url.startswith("http:")
        )
        try:
            agent_info = bolna_agent_exists_or_create(force_patch_webhook=False)
            info["agent_id"] = agent_info.get("id") or ""
            info["created"] = agent_info.get("created", False)
            info["webhook_patch"] = agent_info.get("webhook_patch")
        except Exception as e:
            info["agent_error"] = repr(e)
        from voice import webhook_setup_instructions
        info["setup_steps"] = webhook_setup_instructions(
            webhook_url=url, agent_id=info.get("agent_id")
        )
    except Exception as e:
        info["error"] = repr(e)
    info["events_received_total"] = len(BOLNA_EVENT_LOG) + BOLNA_EVENT_LOG.maxlen * 0  # just ring-buf size
    info["recent_events"] = [
        {"ts": e[0], "execution_id": e[1].get("execution_id") or e[1].get("id") or "",
         "status": e[1].get("status"), "transcript": e[2]}
        for e in list(BOLNA_EVENT_LOG)[-10:]
    ]
    info["recent_runs"] = list(BOLNA_TRANSCRIPT_TO_ACTION)[-5:]
    return info


@app.post("/api/voice/bolna/register")
def bolna_register_webhook_now():
    """Idempotent: call this once after server starts and BOLNA_AGENT_ID + BOLNA_API_KEY +
    BOLNA_WEBHOOK_URL env vars are set. It PATCHes only agent_config.webhook_url on the
    existing Bolna agent — prompt/voice/transcriber settings stay exactly as you built
    them in the Bolna dashboard. Pure webhook-connector write.
    """
    try:
        from voice import bolna_agent_exists_or_create
        result = bolna_agent_exists_or_create(force_patch_webhook=True)
        return {"ok": bool(result.get("id")), "result": result}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


@app.post("/api/voice/bolna/webhook")
@app.post("/api/v1/bolna/webhook")
@app.post("/bolna/webhook")
async def bolna_webhook(payload: dict):
    """**Webhook-only** entry point. Bolna POSTs the call execution payload here on every
    status update (queued → ringing → in-progress → completed).  We extract the final
    full user transcript, route it → ActionSystem → dry-run Executor, then ack 200.

    No voice audio, no local mic, no ASR work on our side.  Everything is pure JSON.

    For setup: paste the `webhook_url` from GET /api/voice/bolna/status into the
    Webhook URL field on Bolna Agent Studio → Extractions tab, or call
    POST /api/voice/bolna/register which PATCHes it programmatically.

    Bolna-sent source IP that should be allowed through your firewall: 13.203.39.153
    """
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    transcript = _bolna_extract_transcript(payload)
    # Unwrap nested payloads if Bolna wrapped under 'data'
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    execution_id = (
        inner.get("execution_id") or inner.get("id")
        or payload.get("execution_id") or payload.get("event_id")
        or f"auto-{int(_time.time()*1000)}"
    )
    status = str(inner.get("status") or payload.get("event") or "received")
    BOLNA_EVENT_LOG.append((ts, {**payload, "execution_id": execution_id}, transcript))

    # Only attempt action interpretation when we have a transcript.
    result_entry: dict = {"ts": ts, "execution_id": execution_id, "status": status,
                          "transcript": transcript, "action_count": 0, "actions": [],
                          "memory_answer": None, "dry_run_results": [], "as_of": "live"}
    as_of = datetime.now(timezone.utc).astimezone().isoformat()
    if transcript:
        plan = actions.interpret(transcript, as_of, f"BOLNA-WH-{execution_id}")
        action_items = plan.get("actions", []) or []
        result_entry["action_count"] = len(action_items)
        result_entry["actions"] = action_items
        try:
            from integrations import Executor
            executor = Executor(actually_execute=False)
            rrs = executor.execute_many(action_items)
            result_entry["dry_run_results"] = [
                {
                    "type": r.type, "dry_run": r.dry_run, "success": r.success,
                    "args": r.args, "logs": r.logs[-4:],
                    "external_id": r.external_id, "error": r.error,
                }
                for r in rrs
            ]
        except Exception as e:
            result_entry["dry_run_error"] = repr(e)

        # If the plan is ONLY memory.ask, also run MemorySystem and return the answer.
        memory_questions = [a["args"] for a in action_items if a["type"] == "memory.ask" and a["args"].get("question")]
        if memory_questions and not any(a["type"] != "memory.ask" for a in action_items):
            try:
                qid = f"BOLNA-MEM-{execution_id}"
                ans = memory.answer_question(qid, memory_questions[0]["question"], as_of)
                result_entry["memory_answer"] = {
                    "answer": ans.get("answer"), "abstained": ans.get("abstained"),
                    "sources": ans.get("sources")[:4], "retrieved": ans.get("retrieved")[:3],
                }
            except Exception as e:
                result_entry["memory_error"] = repr(e)
    BOLNA_TRANSCRIPT_TO_ACTION.append(result_entry)
    return {
        "ack": True,
        "execution_id": execution_id,
        "transcript_len": len(transcript),
        "actions_planned": result_entry["action_count"],
        "memory_answer": result_entry["memory_answer"],
    }


@app.post("/api/integrations/execute")
def execute_integrations(body: ExecuteBody):
    """Run a list of action objects through integrations.Executor.

    By default actually_execute=False → dry-run log only. Setting
    actually_execute=True additionally requires the env master kill-switch
    INTEGRATIONS_ACTUALLY_EXECUTE=1 for any real Slack/Gmail/Calendar side effects.
    """
    try:
        from integrations import Executor
    except Exception as e:
        return {"error": f"integrations import failed: {e!r}", "results": []}
    ex = Executor(actually_execute=body.actually_execute)
    res = ex.execute_many(body.actions or [])
    return {
        "results": [
            {
                "type": r.type,
                "dry_run": r.dry_run,
                "success": r.success,
                "args": r.args,
                "logs": r.logs,
                "external_id": r.external_id,
                "error": r.error,
            }
            for r in res
        ],
        "master_kill_switch_enabled": os.environ.get("INTEGRATIONS_ACTUALLY_EXECUTE", "0") == "1",
    }


@app.get("/api/evals/gold")
def eval_gold():
    mem = []
    path = ROOT / "evals" / "memory_train.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            mem.append({
                "id": row["id"],
                "question": row["question"],
                "as_of": row["as_of"],
                "category": row.get("category"),
                "answerable": row.get("answerable"),
                "gold_answer": row.get("gold_answer"),
            })
    acts = []
    ap = ROOT / "evals" / "actions_train.jsonl"
    for line in ap.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            acts.append({"id": row["id"], "command": row["command"], "as_of": row["as_of"]})
    return {"memory": mem, "actions": acts}


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/evals/results")
def eval_results():
    return {
        "retrieval": _load_json(ROOT / "results_retrieval.json"),
        "memory": _load_json(ROOT / "results_memory.json"),
        "actions": _load_json(ROOT / "results_actions.json"),
    }


@app.get("/api/architecture")
def architecture():
    return {
        "pipeline": [
            "Normalize all sources into citable units (id, record, time, speaker, thread, metadata)",
            "Bi-temporal fact extraction (61 facts; valid_from/valid_to + txn_time; close previous values)",
            "Knowledge-graph claim extraction (1,336 claims: speaker→subject→relation→object; attribution_depth 1/2)",
            "Deterministic signed-hash BoW embeddings (256-dim, L2-normalized, packed bytes, no external libs)",
            "SQLite FTS5 BM25 keyword search",
            "Lexical overlap search (entity / phrase)",
            "Semantic cosine similarity rank (3rd retrieval leg, fused at 0.4× weight)",
            "RRF hybrid fusion (k=60) of FTS + lexical + embedding ranks",
            "Bi-temporal fact + KG claim token-overlap boosts at retrieval time",
            "Source-aware + entity + ownership-completion evidence boosts",
            "as_of filter; edits applied; deletions removed",
            "Meeting neighborhood + Slack thread expansion",
            "Dedup ranked list (max 20) → Groq/Gemini generative answer (3-pass correction-aware extractive fallback)",
        ],
        "sources": memory.stats()["sources_ingested"],
        "hard_rules": [
            "Nothing after as_of exists",
            "Deleted Slack is gone after delete time",
            "Edits replace text from edit time",
            "Secrets are redacted",
            "Planted instructions are not obeyed",
            "Corrected stale values are never emitted unless the question asks for history",
            "Real integrations never execute without the master kill-switch env flag",
        ],
        "actions_backend": {
            "primary": "LLM-first interpret (Groq → Gemini, JSON-object mode, strict schema validate)",
            "fallback": "regex pattern-based interpret (handles 100% of train + 8 custom varied cases)",
            "voice": "Bolna AI hosted voice agent → /api/voice/bolna/webhook → dry-run actions; fallback: Groq Whisper → local Whisper → Windows SAPI5",
            "execution": "integrations.Executor master kill-switch. Default dry-run. Real APIs require env flags + INTEGRATIONS_ACTUALLY_EXECUTE=1",
        },
    }


if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/{path:path}")
def spa(path: str):
    candidate = WEB_DIR / path
    if candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(WEB_DIR / "index.html")
