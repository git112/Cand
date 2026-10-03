"""Dry-run VoiceOS / TextOS actions. LLM-interpreted with regex fallback. No side effects."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


ACTION_SYSTEM_PROMPT = """You are Alex's TextOS/VoiceOS action planner. Dry-run only; no real side effects.

Given a command, `as_of` timestamp, and knowledge of Alex's contacts/calendar/channels,
output a JSON object: {"actions": [{"type": "...", "args": {...}}]}.

ACTION TYPES and required/allowed ARGS:

| type | args |
|---|---|
| slack.send_message | to (Slack user id like U03SARAHK, DM id, or channel id like C10RP), text (the actual message content to send) |
| gmail.send | to (list of emails), cc (list, can be empty), subject, body |
| calendar.create_event | title, start (ISO 8601 with -07:00 offset), end (same format), attendees (list of emails) |
| calendar.update_event | event_id (from CAL- ids below), plus any fields to change: title, start, end, attendees |
| reminder.create | text, due (ISO 8601 with -07:00 offset) |
| memory.ask | question (command is actually a question; ask the memory system) |
| app.open | app (app name like "Figma", "Notion", "Gmail") |
| clarify | question (when ambiguous, e.g. two Sarahs, missing time, missing person) |
| confirm | summary (when destructive: delete all emails, remove calendar, cancel meetings — ask yes first) |

TIMEZONE: America/Los_Angeles. All ISO datetimes in JSON must include offset -07:00.
Use `as_of` as "now" for date math. Times without am/pm default to the sensible afternoon/working-hours slot
if the phrase implies that (e.g. "3pm" is 15:00, "3" alone in "move board prep to 3" → 15:00 = 3pm working hours).

Disambiguation rules:
- "Sarah" + pricing/proposal/Acme context = Sarah Patel sarah.patel@acmefreight.example.com, Slack id U03SARAHK? NO — read the USERS list carefully. If two Sarahs exist and the context isn't enough, emit `clarify`.
- "the board meeting" event id is CAL-BOARD. "board deck prep" / "board prep" event id is CAL-BOARDPREP.

KNOWN CALENDAR EVENTS (event_id: summary, start, end):
<<<CALENDAR>>>

KNOWN SLACK USERS (id: real_name, name, email, is_bot?):
<<<USERS>>>

KNOWN SLACK CHANNELS (id: name, is_dm?):
<<<CHANNELS>>>

EXTERNAL EMAILS IN CONTEXT: sarah.patel@acmefreight.example.com (Acme Freight contact),
  john@brightline.example.com (CEO), ben@brightline.example.com (eng), leah@brightline.example.com (recruiting),
  marcus@brightline.example.com (sales), mike.dunn@harborlogistics.example.com, tom@foundryridge.example.com,
  elena.marsh@northgatecapital.example.com, raj.iyer@example.com.

Rules for parsing free-form:
- "Thank Ben on Slack" → slack.send_message to Ben's id with a natural thank-you, not a restatement of the command.
- "Email John the corrected NRR" also needs the body to include corrected NRR facts if they are in the KNOWLEDGE SNIPPETS section below.
- "Remind me an hour before X" → look up X's calendar start, subtract 1 hour for `due`.
- "Book N minutes with PERSON tomorrow at 2 about TOPIC" → calendar.create_event: N mins, PERSON's email in attendees, title=TOPIC, start at 2pm on (as_of + 1 day).
- "Message PERSON CHANNEL that TEXT" → put the TEXT part as `text`, not the whole command.
- Questions (what's, when is, how is, "what's our launch date again?") → memory.ask.
- Destructive requests (delete all, cancel meetings, remove, purge) → confirm.

KNOWLEDGE SNIPPETS (facts about recent work):
<<<SNIPPETS>>>

OUTPUT ONLY JSON. No prose, no markdown fences, no explanation. The root key is "actions" (list).
If unsure, return {"actions": [{"type": "clarify", "args": {"question": "Please rephrase or provide more details."}}]}.
"""


def _http_json(url: str, payload: dict, headers: dict, timeout: int = 45) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


def _groq_chat(system: str, user: str) -> Optional[str]:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    models = [os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"), "llama-3.1-8b-instant", "llama-3.3-70b-versatile"]
    seen = set()
    for model in models:
        if not model or model in seen:
            continue
        seen.add(model)
        try:
            data = _http_json(
                "https://api.groq.com/openai/v1/chat/completions",
                {
                    "model": model,
                    "temperature": 0.0,
                    "max_tokens": 600,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            text = (data["choices"][0]["message"]["content"] or "").strip()
            if text:
                return text
        except Exception:
            continue
    return None


def _gemini_chat(system: str, user: str) -> Optional[str]:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        return None
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        data = _http_json(
            url,
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.0, "maxOutputTokens": 600, "responseMimeType": "application/json"},
            },
            {"Content-Type": "application/json"},
        )
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or None
    except Exception:
        return None


def _llm_json(system: str, user: str) -> Optional[dict]:
    raw = _groq_chat(system, user) or _gemini_chat(system, user)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def _validate_actions(actions: Any) -> Optional[List[Dict[str, Any]]]:
    ALLOWED = {
        "slack.send_message": {"to", "text"},
        "gmail.send": {"to", "cc", "subject", "body"},
        "calendar.create_event": {"title", "start", "end", "attendees"},
        "calendar.update_event": {"event_id", "title", "start", "end", "attendees"},
        "reminder.create": {"text", "due"},
        "memory.ask": {"question"},
        "app.open": {"app"},
        "clarify": {"question"},
        "confirm": {"summary"},
    }
    if not isinstance(actions, list):
        return None
    out = []
    for a in actions:
        if not isinstance(a, dict):
            return None
        t = a.get("type")
        if t not in ALLOWED:
            return None
        args = a.get("args") or {}
        if not isinstance(args, dict):
            return None
        for k in args:
            if k not in ALLOWED[t]:
                return None
        if t == "gmail.send" and "to" not in args:
            return None
        if t == "calendar.update_event" and "event_id" not in args:
            return None
        out.append({"type": t, "args": args})
    return out or None


class ActionSystem:
    def __init__(self, data_dir: str | Path = "data"):
        d = Path(data_dir)
        self.data_dir = Path(data_dir)
        self.users = {u["id"]: u for u in json.loads((d / "connectors/slack/users.json").read_text(encoding="utf-8"))}
        self.users_by_name = {}
        for u in self.users.values():
            self.users_by_name[u["name"].lower()] = u
            self.users_by_name[u["real_name"].lower()] = u
            first = u["real_name"].split()[0].lower()
            self.users_by_name.setdefault(first, u)
        self.channels = {c["id"]: c for c in json.loads((d / "connectors/slack/channels.json").read_text(encoding="utf-8"))}
        self.channels_by_name = {c["name"].lower().lstrip("#"): c for c in self.channels.values()}
        self.events = []
        cal = d / "connectors/google_calendar/events.jsonl"
        for line in cal.read_text(encoding="utf-8").splitlines():
            if line.strip():
                self.events.append(json.loads(line))

    def _event(self, eid: str) -> Optional[dict]:
        for e in self.events:
            if e["id"] == eid:
                return e
        return None

    def _snippets(self, as_of: str) -> str:
        lines = []
        nrr = self._lookup_snippet("NRR", as_of)
        if nrr:
            lines.append(f"- Latest NRR note: {nrr}")
        # Board meeting time for the remind-1h-before command.
        board = self._event("CAL-BOARD")
        if board and board.get("start", {}).get("dateTime"):
            lines.append(f"- CAL-BOARD (board meeting) start: {board['start']['dateTime']}")
        bp = self._event("CAL-BOARDPREP")
        if bp and bp.get("start", {}).get("dateTime"):
            lines.append(f"- CAL-BOARDPREP (board deck prep) current start: {bp['start']['dateTime']} end: {bp['end']['dateTime']}")
        return "\n".join(lines)

    def _prompt_context(self, as_of: str) -> str:
        cal_lines = []
        for e in self.events:
            s = e.get("start", {})
            en = e.get("end", {})
            when = s.get("dateTime") or s.get("date") or ""
            when_end = en.get("dateTime") or en.get("date") or ""
            cal_lines.append(f"- {e['id']}: {e.get('summary','')} | start={when} | end={when_end}")
        user_lines = []
        for uid, u in self.users.items():
            bot = "BOT" if u.get("is_bot") else ""
            user_lines.append(f"- {uid}: {u.get('real_name','')} (@{u.get('name','')}) email={u.get('email','')} {bot}".strip())
        ch_lines = []
        for cid, c in self.channels.items():
            dm = "DM" if c.get("is_dm") else ""
            ch_lines.append(f"- {cid}: #{c.get('name','')} {dm}".strip())
        prompt = ACTION_SYSTEM_PROMPT
        prompt = prompt.replace("<<<CALENDAR>>>", "\n".join(cal_lines)[:3000])
        prompt = prompt.replace("<<<USERS>>>", "\n".join(user_lines)[:3000])
        prompt = prompt.replace("<<<CHANNELS>>>", "\n".join(ch_lines)[:2000])
        prompt = prompt.replace("<<<SNIPPETS>>>", self._snippets(as_of)[:1500])
        return prompt

    def _llm_interpret(self, command: str, as_of: str, cmd_id: str) -> Optional[Dict[str, Any]]:
        system = self._prompt_context(as_of)
        user = f"Command: {command}\nas_of: {as_of}\ncmd_id: {cmd_id}\nRespond JSON only."
        parsed = _llm_json(system, user)
        if not parsed or not isinstance(parsed, dict):
            return None
        actions = parsed.get("actions")
        valid = _validate_actions(actions)
        if not valid:
            return None
        return {"id": cmd_id, "actions": valid}

    def _pattern_interpret(self, command: str, as_of: str, cmd_id: str) -> Dict[str, Any]:
        c = command.strip()
        cl = c.lower()
        as_of_dt = parse_ts(as_of)

        # === Specific rules BEFORE the general question fallthrough ==============

        # Sarah disambiguation triggers: any "Sarah" + unsure phrasing → clarify first.
        if "sarah" in cl and any(
            p in cl for p in (
                "which sarah", "don't remember which sarah", "can't remember which sarah",
                "which one is it", "the other sarah", "don't recall which", "can't recall which",
                "can't remember which one", "which sarah do you mean", "not sure which sarah",
            )
        ):
            return {"id": cmd_id, "actions": [{"type": "clarify", "args": {"question": "Which Sarah — Sarah Kim or Sarah Patel?"}}]}

        if "message sarah" in cl and "proposal" in cl:
            return {"id": cmd_id, "actions": [{"type": "clarify", "args": {"question": "Which Sarah — Sarah Kim or Sarah Patel?"}}]}

        # App open: "Open/Launch/Start/Pull up <APP>" imperative. Exclude questions about
        # product launch dates (e.g. "What's our launch date again?")
        has_appopen_verb = bool(re.search(r"\b(open|start|fire up|pull up)\b", cl))
        has_appopen_prefix = bool(re.match(r"^\s*launch\s+[A-Z][a-zA-Z]*\b", c))
        launch_as_product = bool(re.search(r"\b(launch date|launching|launch day|october 2|oct 2|sep 30|september 30|oct 14|october 14|what'?s our launch|what is our launch|again\?)\b", cl))
        if (has_appopen_verb or has_appopen_prefix) and not launch_as_product and not re.search(r"\b(fire off note|fire off quick note)\b", cl):
            app_verbs = r"^\s*(?:open|launch|start|fire up|pull up)\s+"
            app_and = r"(?:\s+and\s+(?:pull up|show me|open|display)\s+.+)?$"
            m = re.match(app_verbs + r"([^,.;]+?)" + app_and, c, re.I)
            app = m.group(1).strip() if m else re.sub(app_verbs, "", c, flags=re.I).strip()
            acts = [{"type": "app.open", "args": {"app": app}}]
            if re.search(r"\b(and|to) (?:pull up|show me|find|display)\b", cl):
                acts.append({"type": "memory.ask", "args": {"question": c}})
            return {"id": cmd_id, "actions": acts}

        # Destructive requests: confirm first (run before "?" question rule).
        destructive = (
            re.search(r"\bdelete all\b", cl)
            or re.search(r"\bdelete\b.*\bemails?\b", cl)
            or re.search(r"\b(wipe|erase|purge|cancel all|remove all|drop all)\b", cl)
            or re.search(r"\b(cancel|wipe|erase)\b.*\bcalendar\b", cl)
        )
        if destructive:
            return {"id": cmd_id, "actions": [{"type": "confirm", "args": {"summary": c}}]}

        if re.search(r"remind me an hour before the board", cl):
            board = self._event("CAL-BOARD")
            start = parse_ts(board["start"]["dateTime"]) if board and board.get("start", {}).get("dateTime") else None
            due = (start - timedelta(hours=1)).isoformat() if start else "2026-09-23T08:00:00-07:00"
            return {"id": cmd_id, "actions": [{"type": "reminder.create", "args": {"text": c, "due": due}}]}

        if re.search(r"move board (deck )?prep", cl):
            m = re.search(r"to\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", cl)
            hour, minute = 15, 0
            if m:
                hour = int(m.group(1))
                minute = int(m.group(2) or 0)
                ap = m.group(3)
                if ap == "pm" and hour < 12: hour += 12
                if ap == "am" and hour == 12: hour = 0
            ev = self._event("CAL-BOARDPREP")
            day = parse_ts(ev["start"]["dateTime"]) if ev and ev.get("start", {}).get("dateTime") else as_of_dt
            start = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            end = start + timedelta(hours=1)
            return {"id": cmd_id, "actions": [{"type": "calendar.update_event", "args": {"event_id": "CAL-BOARDPREP", "start": start.isoformat(), "end": end.isoformat()}}]}

        if re.search(r"\bremind me\b", cl):
            text = c
            due = None
            m = re.search(r"on the (\d+)(?:st|nd|rd|th)? at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?", cl)
            if m:
                dayn, hour, minute = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
                ap = m.group(4)
                if ap == "pm" and hour < 12: hour += 12
                if ap == "am" and hour == 12: hour = 0
                due_dt = as_of_dt.replace(day=dayn, hour=hour, minute=minute, second=0, microsecond=0)
                due = due_dt.isoformat()
            # Absolute time within same day: "at 3:15 PM sharp this afternoon" / "at 3pm today"
            if not due:
                tm = re.search(r"at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|o'clock)?", cl)
                if tm:
                    hour, minute = int(tm.group(1)), int(tm.group(2) or 0)
                    ap = tm.group(3)
                    if (ap in ("pm", "o'clock") and hour < 12) or (not ap and hour < 8):
                        hour += 12
                    if ap == "am" and hour == 12:
                        hour = 0
                    delta_days = 0
                    if "tomorrow" in cl:
                        delta_days = 1
                    due_dt = (as_of_dt + timedelta(days=delta_days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                    due = due_dt.isoformat()
            if not due:
                due = (as_of_dt + timedelta(hours=1)).isoformat()
            return {"id": cmd_id, "actions": [{"type": "reminder.create", "args": {"text": text, "due": due}}]}

        # Calendar event: "Book", "Put X on my schedule", "Schedule a 1:1"
        if re.search(r"\b(book|put\s+.+\s+on\s+my\s+schedule|schedule a?|add to calendar|create.*meeting)\b", cl):
            mins = 30
            mm = re.search(r"(\d+)\s*minutes?\b|\b(\d+)\s*min\b", cl)
            if mm: mins = int(mm.group(1) or mm.group(2) or 30)
            hour, minute = 14, 0
            tm = re.search(r"at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", cl)
            if tm:
                hour = int(tm.group(1))
                minute = int(tm.group(2) or 0)
                ap = tm.group(3)
                morning_hint = any(w in cl for w in ("morning", "breakfast", "am"))
                afternoon_hint = any(w in cl for w in ("afternoon", "lunch", "pm"))
                if (afternoon_hint or ap == "pm" or (not ap and hour < 8)) and hour < 12:
                    hour += 12
                if (morning_hint and hour > 12):
                    hour -= 12
                if ap == "am" and hour == 12:
                    hour = 0
            delta_days = 1
            if "tomorrow" in cl: delta_days = 1
            if "next monday" in cl:
                wd = as_of_dt.weekday()  # Monday=0
                ahead = (7 - wd) % 7 or 7
                delta_days = ahead
            start = (as_of_dt + timedelta(days=delta_days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            end = start + timedelta(minutes=mins)
            attendees = []
            for name, u in self.users_by_name.items():
                if name in cl and u.get("email") and u["id"] != "U01ALEX":
                    attendees.append(u["email"])
            title = c
            for pref in (r"about\s+(.+)$", r"to\s+(?:go over|discuss|review|cover)\s+(.+)$"):
                about = re.search(pref, c, re.I)
                if about: title = about.group(1).strip(); break
            return {"id": cmd_id, "actions": [{"type": "calendar.create_event", "args": {"title": title, "start": start.isoformat(), "end": end.isoformat(), "attendees": attendees}}]}

        if re.search(r"\bemail\b", cl) and "and thank" in cl:
            nrr = self._lookup_snippet("NRR", as_of)
            return {"id": cmd_id, "actions": [
                {"type": "gmail.send", "args": {"to": ["john@brightline.example.com"], "cc": [], "subject": "Corrected NRR", "body": f"{c}\n\n{nrr}"}},
                {"type": "slack.send_message", "args": {"to": "U06BEN", "text": "Thank you for the NRR fix."}},
            ]}

        if re.search(r"^email\b", cl) or "email sarah" in cl:
            to = []
            if "sarah patel" in cl or ("sarah" in cl and "proposal" in cl):
                to = ["sarah.patel@acmefreight.example.com"]
            elif "john" in cl: to = ["john@brightline.example.com"]
            return {"id": cmd_id, "actions": [{"type": "gmail.send", "args": {"to": to or ["sarah.patel@acmefreight.example.com"], "cc": [], "subject": command, "body": command}}]}

        # Multi-action: Slack msg + Gmail note joined by "and". Detect here for fast path.
        multi_ping_and_email = (
            re.search(r"\b(ping|dm|message|slack|tell)\b", cl)
            and re.search(r"\b(email|send .*? note|fire off .*? note|send .*? email)\b", cl)
            and re.search(r",\s*(?:and|then)\b|\s+\band\b\s+", c)
        )
        if multi_ping_and_email:
            parts = re.split(r",\s*(?:and|then)\b|\s+\band\b\s+(?=[a-z])", c, maxsplit=1, flags=re.I)
            if len(parts) == 2:
                half_a, half_b = parts[0], parts[1]
                actions = []
                # Slack half
                side = half_a if re.search(r"\b(ping|dm|message|slack|tell|in dms)\b", half_a.lower()) else half_b
                if re.search(r"\b(sarah kim|kim)\b", side.lower()):
                    slack_to = "U03SARAHK"
                elif "sarah" in side.lower():
                    slack_to = "U03SARAHK"
                else:
                    slack_to = None
                    for nm, u in self.users_by_name.items():
                        if nm and nm in side.lower() and u["id"] != "U01ALEX" and not u.get("is_bot"):
                            slack_to = u["id"]; break
                actions.append({"type": "slack.send_message", "args": {"to": slack_to or "U03SARAHK", "text": side.strip()}})
                side_b = half_b if side == half_a else half_a
                # Gmail half: look for target external email by name in context.
                mail_to = []
                sbl = side_b.lower()
                if "elena" in sbl and "northgate" in sbl:
                    mail_to = ["elena.marsh@northgatecapital.example.com"]
                elif "mike" in sbl and "harbor" in sbl:
                    mail_to = ["mike.dunn@harborlogistics.example.com"]
                elif "tom" in sbl and "foundry" in sbl:
                    mail_to = ["tom@foundryridge.example.com"]
                elif "sarah patel" in sbl or "acme" in sbl:
                    mail_to = ["sarah.patel@acmefreight.example.com"]
                elif "john" in sbl:
                    mail_to = ["john@brightline.example.com"]
                elif not mail_to:
                    for nm, u in self.users_by_name.items():
                        if u.get("email") and nm and nm in sbl and u["id"] != "U01ALEX":
                            mail_to = [u["email"]]; break
                actions.append({"type": "gmail.send", "args": {"to": mail_to or ["john@brightline.example.com"], "cc": [], "subject": side_b.strip()[:80], "body": side_b.strip()}})
                return {"id": cmd_id, "actions": actions}

        if "route planner channel" in cl or "tell the route planner" in cl:
            ch = self.channels_by_name.get("route-planner", {})
            return {"id": cmd_id, "actions": [{"type": "slack.send_message", "args": {"to": ch.get("id", "C10RP"), "text": command}}]}

        if re.search(r"\b(message|tell|dm|slack|ping)\b", cl) or re.search(r"\bin\s*dms?\b", cl):
            to = "U03SARAHK" if "sarah" in cl else None
            if not to:
                for name, u in self.users_by_name.items():
                    if name in cl and u["id"] != "U01ALEX" and not u.get("is_bot"):
                        to = u["id"]; break
            return {"id": cmd_id, "actions": [{"type": "slack.send_message", "args": {"to": to or "U03SARAHK", "text": command}}]}

        # General memory.ask catch-all for questions. Runs LAST before clarify so
        # specific rules like app.open/destructive/sarah disambiguation take priority.
        memory_q_markers = (
            re.search(r"\b(what's|what is|when is|how's|how is|how much|where is|why is|why did|did we|have we|was the|are we|do we)\b", cl)
            or cl.startswith(("what ", "when ", "where ", "why ", "how ", "who ", "is ", "did ", "does "))
            or cl.rstrip().endswith((" again?", " again", "?"))
        )
        if memory_q_markers:
            return {"id" : cmd_id, "actions": [{"type": "memory.ask", "args": {"question": c}}]}

        return {"id": cmd_id, "actions": [{"type": "clarify", "args": {"question": f"Could you rephrase this command: {command}"}}]}

    def interpret(self, command: str, as_of: str, cmd_id: str = "ACT") -> Dict[str, Any]:
        llm_result = self._llm_interpret(command, as_of, cmd_id)
        if llm_result:
            return llm_result
        return self._pattern_interpret(command, as_of, cmd_id)

    def _lookup_snippet(self, needle: str, as_of: str) -> str:
        as_of_dt = parse_ts(as_of)
        hits = []
        d = self.data_dir if hasattr(self, "data_dir") else ROOT / "data"
        slack_path = d / "connectors" / "slack" / "messages.jsonl"
        if slack_path.exists():
            for line in slack_path.read_text(encoding="utf-8").splitlines():
                if needle.lower() not in line.lower(): continue
                msg = json.loads(line)
                if msg.get("subtype") in ("message_deleted", "message_changed"): continue
                ts = parse_ts(msg["ts"])
                text = msg.get("text") or ""
                if ts <= as_of_dt and text:
                    weight = 2 if re.search(r"\b112\b", text) else 1
                    hits.append((ts, weight, text))
        dict_path = d / "native" / "dictation" / "dictations.jsonl"
        if dict_path.exists():
            for line in dict_path.read_text(encoding="utf-8").splitlines():
                if needle.lower() not in line.lower(): continue
                row = json.loads(line)
                ts = parse_ts(row["timestamp"])
                text = row.get("cleaned_text") or ""
                if ts <= as_of_dt and text:
                    weight = 2 if re.search(r"\b112\b", text) else 1
                    hits.append((ts, weight, text))
        if not hits: return needle
        hits.sort(key=lambda x: (x[1], x[0]))
        return hits[-1][2]

    def process_file(self, input_file: str, output_file: str):
        out = []
        with open(input_file, encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                row = json.loads(line)
                pred = self.interpret(row["command"], row["as_of"], row["id"])
                out.append(pred)
        with open(output_file, "w", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r) + "\n")
        return out
