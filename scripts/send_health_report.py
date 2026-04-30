#!/usr/bin/env python3
"""Weekly health-check email sender.

Runs `bash scripts/health_check.sh`, parses the output, and emails a
beautifully formatted HTML report using the same SMTP credentials the
PerformanceReporter uses (.env -> SMTP_USER / SMTP_PASSWORD).

Usage:
  # Test immediately (sends one email now):
  python3 scripts/send_health_report.py

  # Cron — every Sunday at 13:00 SAST (= 11:00 UTC if Pi is in UTC,
  # = 13:00 if Pi is in SAST). Add via `crontab -e` on the Pi:
  #   0 13 * * 0 cd /home/diablo/docker/trading-bots/spy-bot && /usr/bin/python3 scripts/send_health_report.py >> logs/health_email.log 2>&1
"""
from __future__ import annotations

import os
import re
import smtplib
import subprocess
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── Paths and .env loading ──────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH    = PROJECT_DIR / ".env"
SCRIPT_PATH = PROJECT_DIR / "scripts" / "health_check.sh"
LOG_PATH    = PROJECT_DIR / "logs" / "health_email.log"


def load_env() -> None:
    """Minimal .env parser — populates os.environ without overwriting."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


# ── Health check execution + parsing ────────────────────────────────────────
ANSI_RE    = re.compile(r"\x1b\[[0-9;]*m")
SECTION_RE = re.compile(r"^──\s+(.+?)\s*$")
STATUS_RE  = re.compile(r"^\s+([✓⚠✗])\s+(PASS|WARN|FAIL)\s+(.+?)(?:\s+—\s+(.+?))?\s*$")
NOTE_RE    = re.compile(r"^\s+↳\s+(.+?)\s*$")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def run_health_check() -> tuple[int, str]:
    """Returns (exit_code, ansi-stripped combined stdout/stderr)."""
    proc = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        timeout=180,
    )
    return proc.returncode, strip_ansi(proc.stdout + proc.stderr)


def parse(raw: str) -> tuple[list[dict], dict, str]:
    """Parse health-check output into structured sections + summary."""
    sections: list[dict] = []
    current: dict | None = None
    summary = {"pass": 0, "warn": 0, "fail": 0}
    verdict = ""
    for line in raw.splitlines():
        if not line.strip():
            continue
        m = SECTION_RE.match(line)
        if m:
            current = {"title": m.group(1).strip(), "items": []}
            sections.append(current)
            continue
        m = STATUS_RE.match(line)
        if m and current is not None:
            symbol, word, label, detail = m.groups()
            kind = word.lower()
            current["items"].append({"kind": kind, "label": label, "detail": detail or ""})
            summary[kind] += 1
            continue
        m = NOTE_RE.match(line)
        if m and current is not None:
            current["items"].append({"kind": "note", "label": m.group(1), "detail": ""})
            continue
        # Catch the bottom verdict banner
        s = line.strip()
        if "READY FOR" in s or "READY WITH" in s or "NOT READY" in s:
            verdict = s
    return sections, summary, verdict


# ── HTML rendering ──────────────────────────────────────────────────────────
SECTION_ICONS = {
    "1.": "📦",  # Containers
    "2.": "🍓",  # Pi hardware
    "3.": "⚙️",  # Configuration
    "4.": "💾",  # Persistence
    "5.": "🌐",  # Alpaca
    "6.": "🧠",  # Engine state
    "7.": "📊",  # Dashboard
    "8.": "📧",  # Email pipeline
    "9.": "🔄",  # Autonomy
}

KIND_ICONS  = {"pass": "✓", "warn": "⚠", "fail": "✗", "note": "·"}
KIND_COLORS = {"pass": "#00E676", "warn": "#FFC107", "fail": "#EF5350", "note": "#9aa0b4"}


def section_icon(title: str) -> str:
    prefix = title.split()[0] if title else ""
    return SECTION_ICONS.get(prefix, "•")


def html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def build_html(sections: list[dict], summary: dict, verdict: str) -> str:
    if summary["fail"] > 0:
        v_color, v_bg, v_icon, v_text = "#EF5350", "rgba(239,83,80,0.10)", "🔴", "NEEDS ATTENTION"
    elif summary["warn"] > 0:
        v_color, v_bg, v_icon, v_text = "#FFC107", "rgba(255,193,7,0.10)", "🟡", "OK WITH WARNINGS"
    else:
        v_color, v_bg, v_icon, v_text = "#00E676", "rgba(0,230,118,0.10)", "🟢", "ALL CLEAR"

    now = datetime.now().strftime("%a, %d %b %Y · %H:%M %Z").strip(" ·")

    body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0a0e1a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#e0e0e0;">
<div style="max-width:680px;margin:0 auto;background:#1a1d2e;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1a1d2e 0%,#0a0e1a 100%);padding:24px 24px 20px;border-bottom:1px solid #2a2f48;">
    <div style="font-size:10px;color:#9aa0b4;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px;">
      🩺 &nbsp;ML Trader · Weekly Health Check
    </div>
    <div style="font-size:18px;font-weight:700;color:#e0e0e0;letter-spacing:0.3px;">
      {html_escape(now)}
    </div>
  </div>

  <!-- Verdict banner -->
  <div style="margin:18px 18px 12px;padding:16px;background:{v_bg};border:1px solid {v_color}40;border-radius:10px;">
    <table cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
      <td style="font-size:26px;width:38px;vertical-align:middle;">{v_icon}</td>
      <td style="vertical-align:middle;">
        <div style="font-size:15px;font-weight:700;color:{v_color};letter-spacing:0.5px;">{v_text}</div>
        <div style="font-size:12px;color:#9aa0b4;margin-top:3px;font-family:monospace;">
          <span style="color:#00E676;">✓ {summary['pass']} pass</span> &nbsp;·&nbsp;
          <span style="color:#FFC107;">⚠ {summary['warn']} warn</span> &nbsp;·&nbsp;
          <span style="color:#EF5350;">✗ {summary['fail']} fail</span>
        </div>
      </td>
    </tr></table>
  </div>
"""

    # Section cards
    for sec in sections:
        icon = section_icon(sec["title"])
        sec_pass = sum(1 for i in sec["items"] if i["kind"] == "pass")
        sec_warn = sum(1 for i in sec["items"] if i["kind"] == "warn")
        sec_fail = sum(1 for i in sec["items"] if i["kind"] == "fail")
        # Section colour-stripe based on worst status in section
        if sec_fail > 0:
            stripe = "#EF5350"
        elif sec_warn > 0:
            stripe = "#FFC107"
        else:
            stripe = "#00E676"

        body += f"""
  <div style="margin:0 18px 12px;background:#0d1117;border:1px solid #1e293b;border-left:3px solid {stripe};border-radius:8px;overflow:hidden;">
    <div style="padding:10px 14px;border-bottom:1px solid #1e293b;">
      <table cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
        <td style="font-size:13px;font-weight:600;color:#e0e0e0;">
          <span style="margin-right:6px;font-size:14px;">{icon}</span>{html_escape(sec['title'])}
        </td>
        <td align="right" style="font-size:10px;color:#64748b;font-family:monospace;white-space:nowrap;">
          ✓{sec_pass} ⚠{sec_warn} ✗{sec_fail}
        </td>
      </tr></table>
    </div>
    <div style="padding:6px 14px 10px;">
"""
        for item in sec["items"]:
            label = html_escape(item["label"])
            detail = html_escape(item["detail"])
            if item["kind"] == "note":
                body += (
                    f'      <div style="padding:3px 0;font-size:11px;color:#64748b;">'
                    f'<span style="color:#475569;margin-right:4px;">↳</span>{label}</div>\n'
                )
            else:
                ic = KIND_ICONS[item["kind"]]
                color = KIND_COLORS[item["kind"]]
                detail_html = (
                    f' <span style="color:#64748b;font-size:11px;">— {detail}</span>'
                    if detail else ""
                )
                body += (
                    f'      <div style="padding:3px 0;font-size:12px;line-height:1.5;">'
                    f'<span style="color:{color};font-weight:bold;display:inline-block;width:14px;">{ic}</span>'
                    f'<span style="color:#e0e0e0;">{label}</span>{detail_html}</div>\n'
                )

        body += "    </div>\n  </div>\n"

    # Footer
    body += f"""
  <div style="padding:16px 24px 20px;border-top:1px solid #2a2f48;font-size:10px;color:#64748b;text-align:center;line-height:1.6;">
    Auto-generated by <span style="font-family:monospace;color:#9aa0b4;">scripts/send_health_report.py</span><br>
    Schedule: every Sunday 13:00 SAST · Re-run anytime: <span style="font-family:monospace;color:#9aa0b4;">bash scripts/health_check.sh</span>
  </div>
</div>
</body>
</html>
"""
    return body


# ── Email sending ───────────────────────────────────────────────────────────
def send_email(html: str, summary: dict) -> bool:
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    to_addr   = (
        os.environ.get("SMTP_TO")
        or os.environ.get("ALERT_EMAIL_ADDRESS")
        or smtp_user
    )

    if not smtp_user or not smtp_pass:
        print("ERROR: SMTP_USER or SMTP_PASSWORD not set in .env", file=sys.stderr)
        return False
    if not to_addr:
        print("ERROR: no recipient address (set SMTP_TO or ALERT_EMAIL_ADDRESS)", file=sys.stderr)
        return False

    if summary["fail"]:
        prefix = "🔴 FAIL"
    elif summary["warn"]:
        prefix = "🟡 WARN"
    else:
        prefix = "🟢 OK"
    subject = f"[ML Trader] {prefix} — Weekly Health Check {datetime.now().strftime('%d %b %Y')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"ML Trader Diablo v1 <{smtp_user}>"
    msg["To"]      = to_addr

    plain = (
        f"Weekly Health Check — {datetime.now().strftime('%d %b %Y')}\n\n"
        f"  PASS: {summary['pass']}\n"
        f"  WARN: {summary['warn']}\n"
        f"  FAIL: {summary['fail']}\n\n"
        f"View this email in HTML for the full report.\n"
    )
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html,  "html",  "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            if smtp_port == 587:
                smtp.starttls()
            smtp.login(smtp_user, smtp_pass)
            smtp.sendmail(smtp_user, [to_addr], msg.as_string())
        print(f"Sent: {subject} → {to_addr}")
        return True
    except Exception as exc:
        print(f"SMTP error: {exc}", file=sys.stderr)
        return False


def main() -> int:
    load_env()
    print(f"[{datetime.now().isoformat(timespec='seconds')}] running health check…")
    code, raw = run_health_check()
    print(f"health_check.sh exit code: {code}")
    sections, summary, verdict = parse(raw)
    print(f"parsed sections={len(sections)} pass={summary['pass']} warn={summary['warn']} fail={summary['fail']}")
    if not sections:
        print("WARNING: no sections parsed — sending raw output as HTML preformatted block")
        html = (
            "<html><body style='background:#0a0e1a;color:#e0e0e0;font-family:monospace;'>"
            "<h2>Health check produced no parseable sections</h2><pre>"
            + html_escape(raw or "(empty output)") +
            "</pre></body></html>"
        )
    else:
        html = build_html(sections, summary, verdict)

    ok = send_email(html, summary)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
