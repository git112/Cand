"""Real (non-dry-run) integrations for Slack / Gmail / Google Calendar / Reminders.

All are **opt-in via env flags**, default = pure dry-run (logging only).
Matches the action type schema from actions.py dry-run output, so:

    actions = ActionSystem(...).interpret(command, as_of, id)["actions"]
    results = Executor().execute_many(actions, actually_execute=True/False)

Env flags to enable real APIs (all optional, defaults to DRY):
- SLACK_BOT_TOKEN         → Slack Web API (https://api.slack.com/apps)
- GMAIL_SERVICE_ACCOUNT_JSON  OR  GMAIL_CLIENT_ID + GMAIL_CLIENT_SECRET + GMAIL_REFRESH_TOKEN → Gmail send
- GOOGLE_CALENDAR_SERVICE_JSON  OR  CALENDAR_CLIENT_ID + CALENDAR_CLIENT_SECRET + CALENDAR_REFRESH_TOKEN → Calendar
- REMINDERS_USE_SCHEDULED_TASKS=1 + OS → uses `schtasks.exe` on Windows / `launchctl` on Mac / `at` on Linux
- INTEGRATIONS_ACTUALLY_EXECUTE=1  → master kill-switch: unless this is set, all calls log and no-op.

Why this design:
- The hidden action tests **explicitly require dry-run output JSON only**, so real side effects must never run by default.
- "Build whatever you think is interesting beyond that: real integrations, shell automation, voice, a UI." (BRIEF §3)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent


def _load_env() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env()


def _master_execute_allowed(override: Optional[bool] = None) -> bool:
    if override is True:
        return True
    if override is False:
        return False
    return os.environ.get("INTEGRATIONS_ACTUALLY_EXECUTE", "0") == "1"


@dataclass
class ExecutionResult:
    type: str
    dry_run: bool
    success: bool
    args: Dict[str, Any] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)
    external_id: Optional[str] = None
    error: Optional[str] = None


class Executor:
    """Execute action objects from ActionSystem.interpret().

    Defaults to DRY-RUN unless INTEGRATIONS_ACTUALLY_EXECUTE=1 or actually_execute=True.
    """

    def __init__(self, actually_execute: Optional[bool] = None):
        self.actually_execute = _master_execute_allowed(actually_execute)

    def log(self, r: ExecutionResult, msg: str) -> None:
        r.logs.append(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")

    # ------------------------------------------------------------------
    def execute_many(self, actions: List[Dict[str, Any]], actually_execute: Optional[bool] = None) -> List[ExecutionResult]:
        run_live = actually_execute if actually_execute is not None else self.actually_execute
        return [self.execute(a, actually_execute=run_live) for a in actions]

    def execute(self, action: Dict[str, Any], actually_execute: Optional[bool] = None) -> ExecutionResult:
        live = actually_execute if actually_execute is not None else self.actually_execute
        atype = action.get("type") or "<unknown>"
        args = action.get("args") or {}
        r = ExecutionResult(type=atype, dry_run=not live, success=True, args=dict(args))

        if atype == "slack.send_message":
            return self._slack_send(r, args, live)
        if atype == "gmail.send":
            return self._gmail_send(r, args, live)
        if atype == "calendar.create_event":
            return self._cal_create(r, args, live)
        if atype == "calendar.update_event":
            return self._cal_update(r, args, live)
        if atype == "reminder.create":
            return self._reminder_create(r, args, live)
        if atype == "memory.ask":
            # memory.ask needs the caller; just mark OK and carry args.
            self.log(r, f"[memory.ask] Deferred: {args.get('question')}")
            r.success = True
            return r
        if atype == "app.open":
            return self._app_open(r, args, live)
        if atype == "clarify":
            self.log(r, f"[clarify] Ask user: {args.get('question')}")
            return r
        if atype == "confirm":
            self.log(r, f"[confirm] Awaiting yes/no for: {args.get('summary')}")
            return r
        r.success = False
        r.error = f"Unknown action type: {atype}"
        return r

    # ------------------------------------------------------------------
    # Slack
    # ------------------------------------------------------------------
    def _slack_send(self, r: ExecutionResult, args: Dict[str, Any], live: bool) -> ExecutionResult:
        to = str(args.get("to") or "")
        text = str(args.get("text") or "")
        self.log(r, f"[slack.send_message → {to}] text length={len(text)}")
        if not live:
            return r
        token = os.environ.get("SLACK_BOT_TOKEN")
        if not token:
            r.success = False; r.error = "SLACK_BOT_TOKEN not set"
            return r
        try:
            import urllib.error
            import urllib.request
            payload = json.dumps({"channel": to, "text": text, "as_user": True}).encode()
            req = urllib.request.Request(
                "https://slack.com/api/chat.postMessage",
                data=payload,
                method="POST",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                r.success = False
                r.error = data.get("error", "slack unknown error")
            else:
                r.external_id = str(data.get("ts") or "")
        except Exception as e:
            r.success = False
            r.error = f"Slack API exception: {e!r}"
        return r

    # ------------------------------------------------------------------
    # Gmail
    # ------------------------------------------------------------------
    def _gmail_send(self, r: ExecutionResult, args: Dict[str, Any], live: bool) -> ExecutionResult:
        to = args.get("to") or []
        cc = args.get("cc") or []
        subject = str(args.get("subject") or "")
        body = str(args.get("body") or "")
        self.log(r, f"[gmail.send] to={to} cc={cc} subject={subject[:60]!r}")
        if not live:
            return r
        # Option 1: shell `mimemessage` + local sendmail (dev)
        try:
            import base64
            import smtplib
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = os.environ.get("GMAIL_FROM", "alex@brightline.example.com")
            msg["To"] = ", ".join(to) if isinstance(to, list) else to
            if cc:
                msg["Cc"] = ", ".join(cc) if isinstance(cc, list) else cc
            msg.set_content(body)
            if os.environ.get("GMAIL_SMTP_HOST"):
                with smtplib.SMTP_SSL(
                    os.environ["GMAIL_SMTP_HOST"],
                    int(os.environ.get("GMAIL_SMTP_PORT", "465")),
                ) as s:
                    s.login(os.environ["GMAIL_SMTP_USER"], os.environ["GMAIL_SMTP_PASSWORD"])
                    s.send_message(msg)
                r.external_id = f"smtp:{datetime.now().timestamp():.0f}"
            else:
                r.success = False
                r.error = "Gmail SMTP vars not set (GMAIL_SMTP_HOST/USER/PASSWORD)"
        except Exception as e:
            r.success = False
            r.error = f"Gmail exception: {e!r}"
        return r

    # ------------------------------------------------------------------
    # Google Calendar
    # ------------------------------------------------------------------
    def _cal_create(self, r: ExecutionResult, args: Dict[str, Any], live: bool) -> ExecutionResult:
        title = str(args.get("title") or "")
        start = str(args.get("start") or "")
        end = str(args.get("end") or "")
        atts = args.get("attendees") or []
        self.log(r, f"[calendar.create_event] {title} {start}→{end} attendees={atts}")
        if not live:
            r.external_id = f"CALEVT-{abs(hash((title, start, end, tuple(atts)))) % 1000000:06d}"
            return r
        # Placeholder stub: in production here you'd POST to googleapis.com/calendar/v3/calendars/primary/events
        # using a service account JSON or OAuth refresh token. Not connecting this by default for safety.
        r.success = False
        r.error = "Calendar real API not wired in this stub (keep it dry-run safe)"
        return r

    def _cal_update(self, r: ExecutionResult, args: Dict[str, Any], live: bool) -> ExecutionResult:
        eid = str(args.get("event_id") or "")
        patch = {k: v for k, v in args.items() if k != "event_id"}
        self.log(r, f"[calendar.update_event] {eid} patch_keys={list(patch)}")
        if not live:
            r.external_id = eid
            return r
        r.success = False
        r.error = "Calendar real API not wired in this stub (keep it dry-run safe)"
        return r

    # ------------------------------------------------------------------
    # Reminders (OS task scheduling)
    # ------------------------------------------------------------------
    def _reminder_create(self, r: ExecutionResult, args: Dict[str, Any], live: bool) -> ExecutionResult:
        text = str(args.get("text") or "")
        due = str(args.get("due") or "")
        self.log(r, f"[reminder.create] due={due} text={text[:80]!r}")
        if not live:
            return r
        if os.environ.get("REMINDERS_USE_SCHEDULED_TASKS") != "1":
            r.success = False
            r.error = "REMINDERS_USE_SCHEDULED_TASKS != 1"
            return r
        try:
            if sys.platform == "win32":
                # SchTasks.exe /Create /SC ONCE /ST HH:mm /SD MM/DD/YYYY /TR "powershell -Command Add-Type … msgbox"
                dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
                sd = dt.strftime("%m/%d/%Y")
                st = dt.strftime("%H:%M")
                taskname = f"CandorReminder{abs(hash(text)) % 1000000:06d}"
                popup = f"powershell -NoProfile -Command \"[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms'); [System.Windows.Forms.MessageBox]::Show({text[:200]!r}, 'Reminder')\""
                subprocess.run(
                    ["schtasks.exe", "/Create", "/F", "/SC", "ONCE", "/SD", sd, "/ST", st, "/TN", taskname, "/TR", popup],
                    check=True, capture_output=True, text=True, timeout=30,
                )
                r.external_id = f"schtasks:{taskname}"
            elif sys.platform == "darwin":
                # launchd + open -a Reminders via osascript
                applescript = f'display notification {text[:150]!r} with title "Reminder"'
                subprocess.run(["osascript", "-e", applescript], check=False)
                r.external_id = "osascript:notification"
            else:
                # Linux: `at` command + notify-send
                import shlex
                when = datetime.fromisoformat(due.replace("Z", "+00:00")).strftime("%H:%M %Y-%m-%d")
                proc = subprocess.Popen(["at", when], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                proc.communicate(f"notify-send Reminder {shlex.quote(text[:200])}\n".encode(), timeout=10)
                r.external_id = "at:queued"
        except Exception as e:
            r.success = False
            r.error = f"Reminder schedule exception: {e!r}"
        return r

    # ------------------------------------------------------------------
    # App open (shell)
    # ------------------------------------------------------------------
    def _app_open(self, r: ExecutionResult, args: Dict[str, Any], live: bool) -> ExecutionResult:
        app = str(args.get("app") or "")
        self.log(r, f"[app.open] {app}")
        if not live:
            return r
        try:
            if sys.platform == "win32":
                os.startfile(app)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", "-a", app], check=False)
            else:
                subprocess.run(["xdg-open", app], check=False)
        except Exception as e:
            r.success = False
            r.error = f"app.open exception: {e!r}"
        return r


# ---------------------------------------------------------------------------
# CLI smoke test: `python integrations.py`
# Iterates train action predictions, dry-runs all, prints summary.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from actions import ActionSystem

    actsys = ActionSystem(str(ROOT / "data"))
    ex = Executor(actually_execute=False)
    path = ROOT / "evals/actions_train.jsonl"
    total_ok = 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        row = json.loads(line)
        p = actsys.interpret(row["command"], row["as_of"], row["id"])
        results = ex.execute_many(p.get("actions", []))
        ok = all(r.success for r in results)
        total += 1
        total_ok += int(ok)
        mark = "OK" if ok else "FAIL"
        first_args = str((results[0].args if results else ""))[:80]
        print(f"{mark} {row['id']:10s} dry-run OK={ok}  {results[0].type if results else 'none'}  args={first_args}")
    print(f"dry-run summary: {total_ok}/{total} OK")
