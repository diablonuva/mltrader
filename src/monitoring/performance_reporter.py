"""Performance Reporter — scheduled HTML email reports for ML Trader.

Sends four report types, all triggered at EOD (session close):
  daily   — every trading day
  weekly  — every Friday
  monthly — last trading day of each calendar month
  8-week  — every 8th Friday from the first run date (paper-trading review cycle)

Also sends an immediate HTML alert whenever the circuit breaker fires.

SMTP setup (Gmail recommended):
  1. Enable 2-Step Verification on your Google account.
  2. Google Account → Security → App Passwords → generate a password for Mail.
  3. Add to .env:
       SMTP_USER=your.address@gmail.com
       SMTP_PASSWORD=xxxx xxxx xxxx xxxx   (16-char App Password, spaces optional)
  4. Ensure settings.yaml has:
       smtp:
         host: smtp.gmail.com
         port: 587
         user: ""          # overridden by SMTP_USER env var
         password: ""      # overridden by SMTP_PASSWORD env var
         from_address: "ML Trader <your.address@gmail.com>"
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import traceback
from collections import Counter
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PAPER_BADGE = (
    '<span style="background:#546E7A;color:#fff;padding:2px 8px;'
    'border-radius:4px;font-size:12px;font-weight:700;">PAPER</span>'
)
_LIVE_BADGE = (
    '<span style="background:#EF5350;color:#fff;padding:2px 8px;'
    'border-radius:4px;font-size:12px;font-weight:700;">LIVE</span>'
)

_STATE_FILE = "logs/reporter_state.json"


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _pct_color(v: float) -> str:
    return "#00E676" if v >= 0 else "#EF5350"


def _fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v * 100:.2f}%"


def _fmt_dollar(v: float) -> str:
    return f"${v:,.2f}"


def _html_wrap(title: str, period_label: str, body: str, is_paper: bool) -> str:
    """Wrap body content in a full HTML email shell."""
    badge = _PAPER_BADGE if is_paper else _LIVE_BADGE
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;padding:0;background:#0e1117;font-family:Arial,sans-serif;color:#e0e0e0;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0e1117;">
<tr><td align="center" style="padding:24px 12px;">
<table width="620" cellpadding="0" cellspacing="0"
       style="background:#161b2e;border-radius:12px;overflow:hidden;
              border:1px solid #1f2235;">

  <!-- Header -->
  <tr><td style="background:#1a237e;padding:20px 28px;">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td>
          <div style="font-size:11px;color:#90caf9;letter-spacing:0.1em;
                      text-transform:uppercase;margin-bottom:4px;">
            {period_label} PERFORMANCE REPORT
          </div>
          <div style="font-size:20px;font-weight:700;color:#fff;">
            🧠 ML Trader — Diablo v1
          </div>
        </td>
        <td align="right" style="vertical-align:top;">
          {badge}
          <div style="font-size:11px;color:#90caf9;margin-top:6px;">{now_str}</div>
        </td>
      </tr>
    </table>
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:24px 28px;">
    {body}
  </td></tr>

  <!-- Footer -->
  <tr><td style="background:#0d1117;padding:14px 28px;border-top:1px solid #1f2235;">
    <div style="font-size:11px;color:#546E7A;text-align:center;">
      ML Trader Diablo v1 · Automated report · Do not reply
    </div>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""


def _kpi_row(*items: tuple[str, str, str]) -> str:
    """Render a row of KPI cards. Each item is (label, value, color)."""
    cells = ""
    width = 100 // len(items)
    for label, value, color in items:
        cells += f"""
        <td width="{width}%" style="padding:4px;">
          <div style="background:#0e1117;border-radius:8px;padding:12px 14px;
                      border:1px solid #1f2235;text-align:center;">
            <div style="font-size:11px;color:#9aa0b4;text-transform:uppercase;
                        letter-spacing:0.05em;margin-bottom:4px;">{label}</div>
            <div style="font-size:20px;font-weight:700;color:{color};">{value}</div>
          </div>
        </td>"""
    return f'<table width="100%" cellpadding="0" cellspacing="0"><tr>{cells}</tr></table>'


def _section_header(text: str) -> str:
    return (
        f'<div style="font-size:13px;font-weight:700;color:#90caf9;'
        f'text-transform:uppercase;letter-spacing:0.08em;'
        f'border-bottom:1px solid #1f2235;padding-bottom:8px;'
        f'margin:24px 0 14px 0;">{text}</div>'
    )


def _table(headers: list[str], rows: list[list[str]], col_align: list[str] | None = None) -> str:
    """Generic HTML table matching dashboard dark theme."""
    if col_align is None:
        col_align = ["left"] * len(headers)
    th_style = ("background:#0d1117;color:#9aa0b4;font-size:11px;"
                "text-transform:uppercase;letter-spacing:0.06em;padding:8px 10px;"
                "border-bottom:1px solid #1f2235;")
    td_style = "color:#e0e0e0;font-size:13px;padding:8px 10px;border-bottom:1px solid #1a1e2e;"

    head_cells = "".join(
        f'<th style="{th_style}text-align:{a};">{h}</th>'
        for h, a in zip(headers, col_align)
    )
    body_rows = ""
    for row in rows:
        cells = "".join(
            f'<td style="{td_style}text-align:{a};">{v}</td>'
            for v, a in zip(row, col_align)
        )
        body_rows += f"<tr>{cells}</tr>"

    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;border:1px solid #1f2235;'
        f'border-radius:6px;overflow:hidden;">'
        f"<thead><tr>{head_cells}</tr></thead>"
        f"<tbody>{body_rows}</tbody></table>"
    )


# ---------------------------------------------------------------------------
# Trade data helpers
# ---------------------------------------------------------------------------

def _read_trades(log_dir: str, since: Optional[date] = None) -> list[dict]:
    """Read all (or post-since) trades from trades.log (JSON Lines)."""
    path = Path(log_dir) / "trades.log"
    trades: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if since is not None:
                        ts_str = rec.get("ts", rec.get("entry_time", ""))
                        if ts_str:
                            try:
                                ts_date = datetime.fromisoformat(ts_str).date()
                                if ts_date < since:
                                    continue
                            except Exception:
                                pass
                    trades.append(rec)
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return trades


def _compute_stats(trades: list[dict]) -> dict:
    """Compute summary stats from a list of trade records."""
    full = [t for t in trades if not t.get("is_partial", False)]
    if not full:
        return {
            "n": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "avg_pnl_pct": 0.0, "total_pnl_dollar": 0.0,
            "best_pct": 0.0, "worst_pct": 0.0,
            "exit_counts": {}, "regime_counts": {},
        }
    pnls = [t.get("pnl_pct", 0.0) for t in full]
    pnl_dollars = [t.get("pnl_dollar", 0.0) for t in full]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    exit_counts: dict[str, dict] = {}
    for t in full:
        reason = t.get("exit_reason", "UNKNOWN")
        if reason not in exit_counts:
            exit_counts[reason] = {"count": 0, "pnl_sum": 0.0, "wins": 0}
        exit_counts[reason]["count"] += 1
        exit_counts[reason]["pnl_sum"] += t.get("pnl_pct", 0.0)
        if t.get("pnl_pct", 0.0) > 0:
            exit_counts[reason]["wins"] += 1
    regime_counts: dict[str, dict] = {}
    for t in full:
        r = t.get("regime_at_entry", "UNKNOWN")
        if r not in regime_counts:
            regime_counts[r] = {"count": 0, "pnl_sum": 0.0, "wins": 0}
        regime_counts[r]["count"] += 1
        regime_counts[r]["pnl_sum"] += t.get("pnl_pct", 0.0)
        if t.get("pnl_pct", 0.0) > 0:
            regime_counts[r]["wins"] += 1
    return {
        "n": len(full),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(full) if full else 0.0,
        "avg_pnl_pct": sum(pnls) / len(pnls) if pnls else 0.0,
        "total_pnl_dollar": sum(pnl_dollars),
        "best_pct": max(pnls) if pnls else 0.0,
        "worst_pct": min(pnls) if pnls else 0.0,
        "exit_counts": exit_counts,
        "regime_counts": regime_counts,
        "recent": sorted(full, key=lambda t: t.get("ts", ""), reverse=True)[:10],
    }


# ---------------------------------------------------------------------------
# Report body builders
# ---------------------------------------------------------------------------

def _build_performance_body(
    period_label: str,
    stats: dict,
    equity_start: float,
    equity_end: float,
    date_range: str,
) -> str:
    equity_chg = equity_end - equity_start
    equity_chg_pct = equity_chg / equity_start if equity_start else 0.0
    eq_color = _pct_color(equity_chg_pct)

    body = ""

    # Date range
    body += (
        f'<div style="font-size:13px;color:#9aa0b4;margin-bottom:18px;">'
        f'Period: <strong style="color:#e0e0e0;">{date_range}</strong></div>'
    )

    # KPI row 1 — equity
    body += _section_header("Equity")
    body += _kpi_row(
        ("Start Equity", _fmt_dollar(equity_start), "#e0e0e0"),
        ("End Equity", _fmt_dollar(equity_end), eq_color),
        ("Period Return", _fmt_pct(equity_chg_pct), eq_color),
    )

    # KPI row 2 — trades
    body += _section_header("Trade Statistics")
    if stats["n"] == 0:
        body += '<div style="color:#9aa0b4;padding:12px 0;">No trades in this period.</div>'
    else:
        body += _kpi_row(
            ("Total Trades", str(stats["n"]), "#e0e0e0"),
            ("Win Rate", f"{stats['win_rate']*100:.1f}%",
             _pct_color(stats["win_rate"] - 0.5)),
            ("Avg PnL", _fmt_pct(stats["avg_pnl_pct"]),
             _pct_color(stats["avg_pnl_pct"])),
            ("Net PnL $", _fmt_dollar(stats["total_pnl_dollar"]),
             _pct_color(stats["total_pnl_dollar"])),
        )
        body += "<br>"
        body += _kpi_row(
            ("Best Trade", _fmt_pct(stats["best_pct"]),
             _pct_color(stats["best_pct"])),
            ("Worst Trade", _fmt_pct(stats["worst_pct"]),
             _pct_color(stats["worst_pct"])),
            ("Wins", str(stats["wins"]), "#00E676"),
            ("Losses", str(stats["losses"]), "#EF5350"),
        )

        # Exit reason table
        if stats["exit_counts"]:
            body += _section_header("Exit Reason Breakdown")
            rows = []
            for reason, d in sorted(
                stats["exit_counts"].items(), key=lambda x: -x[1]["count"]
            ):
                avg = d["pnl_sum"] / d["count"] if d["count"] else 0.0
                wr = d["wins"] / d["count"] * 100 if d["count"] else 0.0
                rows.append([
                    reason,
                    str(d["count"]),
                    f'<span style="color:{_pct_color(avg)};">{_fmt_pct(avg)}</span>',
                    f'<span style="color:{_pct_color(wr/100-0.5)};">{wr:.1f}%</span>',
                ])
            body += _table(
                ["Exit Reason", "Count", "Avg PnL", "Win Rate"],
                rows,
                ["left", "right", "right", "right"],
            )

        # Regime breakdown table
        if stats["regime_counts"]:
            body += _section_header("Regime Breakdown")
            rows = []
            for regime, d in sorted(
                stats["regime_counts"].items(), key=lambda x: -x[1]["count"]
            ):
                avg = d["pnl_sum"] / d["count"] if d["count"] else 0.0
                wr = d["wins"] / d["count"] * 100 if d["count"] else 0.0
                rows.append([
                    regime,
                    str(d["count"]),
                    f'<span style="color:{_pct_color(avg)};">{_fmt_pct(avg)}</span>',
                    f'<span style="color:{_pct_color(wr/100-0.5)};">{wr:.1f}%</span>',
                ])
            body += _table(
                ["Regime", "Trades", "Avg PnL", "Win Rate"],
                rows,
                ["left", "right", "right", "right"],
            )

        # Recent trades
        if stats.get("recent"):
            body += _section_header(f"Recent Trades (last {len(stats['recent'])})")
            rows = []
            for t in stats["recent"]:
                pnl = t.get("pnl_pct", 0.0)
                rows.append([
                    t.get("ts", "")[:10],
                    t.get("asset", ""),
                    t.get("direction", ""),
                    t.get("regime_at_entry", ""),
                    f'<span style="color:{_pct_color(pnl)};">{_fmt_pct(pnl)}</span>',
                    t.get("exit_reason", ""),
                ])
            body += _table(
                ["Date", "Asset", "Dir", "Regime", "PnL", "Exit"],
                rows,
                ["left", "left", "left", "left", "right", "left"],
            )

    return body


def _build_engine_state_section(engine_state: dict) -> str:
    """Optional 'Today's Engine Activity' section for the daily email.

    engine_state is a free-form dict produced by MLTrader._build_engine_state_for_email.
    All keys are optional — missing keys are silently skipped so the format
    stays forward-compatible.
    """
    body = _section_header("Today's Engine Activity")

    # Top-line KPIs: HMM trained, regimes detected, signals, positions
    cb_active = engine_state.get("circuit_breaker_active", False)
    cb_color = "#EF5350" if cb_active else "#00E676"
    cb_text  = "TRIPPED" if cb_active else "OK"

    body += _kpi_row(
        ("HMM Trained", "✓ Yes" if engine_state.get("hmm_trained") else "✗ No",
         "#00E676" if engine_state.get("hmm_trained") else "#EF5350"),
        ("Bars Archived", str(engine_state.get("bars_archived", 0)), "#e0e0e0"),
        ("Signals Today", str(engine_state.get("signals_today", 0)), "#e0e0e0"),
        ("Open Positions @ EOD", str(engine_state.get("open_positions", 0)),
         "#FFC107" if engine_state.get("open_positions", 0) > 0 else "#9aa0b4"),
    )
    body += "<br>"
    body += _kpi_row(
        ("Circuit Breaker", cb_text, cb_color),
        ("Trader Uptime", engine_state.get("uptime", "?"), "#e0e0e0"),
        ("Bars Today", str(engine_state.get("bars_today", 0)), "#e0e0e0"),
    )

    # Per-asset regime snapshot
    per_asset = engine_state.get("per_asset", {})
    if per_asset:
        rows = []
        for asset, info in per_asset.items():
            regime = info.get("regime", "UNKNOWN")
            conf   = info.get("confidence", 0.0)
            warmup = info.get("warmup", "?")
            rows.append([
                asset,
                regime.replace("_", " "),
                f"{conf*100:.0f}%",
                warmup,
            ])
        body += _table(
            ["Asset", "Regime @ Close", "Confidence", "Feature Warmup"],
            rows,
            ["left", "left", "right", "right"],
        )

    # Signal-reason histogram (why no trades, when applicable)
    reasons = engine_state.get("signal_reasons", {})
    if reasons:
        body += _section_header("Signal Outcomes (today)")
        rows = sorted(reasons.items(), key=lambda x: -x[1])
        body += _table(
            ["Reason", "Count"],
            [[r, str(c)] for r, c in rows],
            ["left", "right"],
        )

    return body


def _build_circuit_breaker_body(
    reason: str,
    equity: float,
    timestamp: datetime,
    recent_trades: list[dict],
) -> str:
    body = ""
    body += (
        '<div style="background:#b71c1c;border-radius:8px;padding:16px 18px;'
        'margin-bottom:20px;">'
        '<div style="font-size:16px;font-weight:700;color:#fff;">🚨 Circuit Breaker Activated</div>'
        f'<div style="font-size:13px;color:#ffcdd2;margin-top:6px;">Reason: <strong>{reason}</strong></div>'
        f'<div style="font-size:13px;color:#ffcdd2;">Time: {timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")}</div>'
        '</div>'
    )
    body += _kpi_row(
        ("Account Equity", _fmt_dollar(equity), "#e0e0e0"),
        ("Trigger", reason, "#EF5350"),
        ("Status", "HALTED", "#EF5350"),
    )
    body += (
        '<div style="background:#1a237e;border-radius:6px;padding:12px 14px;'
        'margin-top:16px;font-size:13px;color:#90caf9;">'
        '⚠️ <strong>Action required:</strong> Review logs before restarting the engine. '
        'Check <code>logs/session.log</code> for the full event sequence. '
        'Ensure no open positions remain in your Alpaca account.'
        '</div>'
    )
    if recent_trades:
        body += _section_header("Trades Leading to Circuit Breaker")
        rows = []
        for t in recent_trades[-5:]:
            pnl = t.get("pnl_pct", 0.0)
            rows.append([
                t.get("ts", "")[:19],
                t.get("asset", ""),
                t.get("regime_at_entry", ""),
                f'<span style="color:{_pct_color(pnl)};">{_fmt_pct(pnl)}</span>',
                t.get("exit_reason", ""),
            ])
        body += _table(
            ["Time", "Asset", "Regime", "PnL", "Exit Reason"],
            rows,
            ["left", "left", "left", "right", "left"],
        )
    return body


# ---------------------------------------------------------------------------
# Scheduler state
# ---------------------------------------------------------------------------

def _load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(path: str, state: dict) -> None:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
    except Exception as exc:
        logger.warning("PerformanceReporter: could not save state: %s", exc)


def _is_last_trading_day_of_month(today: date) -> bool:
    """True if today is the last weekday of the current calendar month."""
    import calendar
    last_day = calendar.monthrange(today.year, today.month)[1]
    for d in range(last_day, today.day - 1, -1):
        candidate = today.replace(day=d)
        if candidate.weekday() < 5:  # Mon–Fri
            return candidate == today
    return False


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class PerformanceReporter:
    """Composes and dispatches HTML performance reports via SMTP.

    Call ``on_session_close()`` at EOD each trading day.
    Call ``on_circuit_breaker()`` immediately when a circuit breaker fires.
    """

    def __init__(self, config: dict) -> None:
        mon = config.get("monitoring", {})
        smtp = config.get("smtp", {})

        self._enabled: bool = bool(mon.get("alert_email_enabled", False))
        self._to_address: str = mon.get("alert_email_address", "")
        self._log_dir: str = mon.get("log_dir", "logs")

        # SMTP — env vars override config values
        self._smtp_host: str = smtp.get("host", "smtp.gmail.com")
        self._smtp_port: int = int(smtp.get("port", 587))
        self._smtp_user: str = (
            os.environ.get("SMTP_USER") or smtp.get("user", "")
        )
        self._smtp_password: str = (
            os.environ.get("SMTP_PASSWORD") or smtp.get("password", "")
        )
        self._smtp_from: str = smtp.get(
            "from_address",
            f"ML Trader Diablo v1 <{self._smtp_user}>",
        )

        # Paper / live detection
        base_url = os.environ.get("ALPACA_BASE_URL", "")
        self._is_paper: bool = "paper" in base_url.lower() or not base_url

        # State file for 8-week tracking
        self._state_path: str = str(Path(self._log_dir) / "reporter_state.json")
        self._state: dict = _load_state(self._state_path)

        # Track initial equity for 8-week return calculation
        self._initial_equity: Optional[float] = self._state.get("initial_equity")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _isoweek_key(d: date) -> str:
        """Idempotency key for weekly reports (e.g. '2026-W18')."""
        year, week, _ = d.isocalendar()
        return f"{year}-W{week:02d}"

    @staticmethod
    def _month_key(d: date) -> str:
        """Idempotency key for monthly reports (e.g. '2026-04')."""
        return f"{d.year}-{d.month:02d}"

    def was_daily_sent_today(self, today: date) -> bool:
        """True iff a daily email was successfully delivered for `today`."""
        return self._state.get("last_daily_report") == today.isoformat()

    def was_weekly_sent_for_isoweek(self, today: date) -> bool:
        """True iff a weekly email was successfully delivered for `today`'s ISO week."""
        return self._state.get("last_weekly_report") == self._isoweek_key(today)

    def was_monthly_sent_for_month(self, today: date) -> bool:
        """True iff a monthly email was successfully delivered for `today`'s month."""
        return self._state.get("last_monthly_report") == self._month_key(today)

    def on_session_close(
        self,
        today: date,
        equity_end: float,
        equity_start_of_day: float,
        engine_state: Optional[dict] = None,
    ) -> None:
        """Call at EOD. Sends whichever reports are due for today.

        Each report type (daily/weekly/monthly/8-week) is independently
        idempotent — repeated calls within the same period are safe and
        will retry only the reports that haven't been successfully sent.

        Pass `engine_state` (a dict from MLTrader._build_engine_state_for_email)
        to include an Engine Activity section in the daily email body.
        """
        if not self._enabled or not self._to_address:
            return

        # Bootstrap initial equity on first-ever run
        if self._initial_equity is None:
            self._initial_equity = equity_start_of_day
            self._state["initial_equity"] = self._initial_equity
            self._state["launch_date"] = today.isoformat()
            _save_state(self._state_path, self._state)

        all_trades = _read_trades(self._log_dir)
        today_trades = _read_trades(self._log_dir, since=today)

        # --- Daily ---
        if not self.was_daily_sent_today(today):
            sent_ok = self._send_daily(today, today_trades, equity_start_of_day, equity_end, engine_state)
            if sent_ok:
                self._state["last_daily_report"] = today.isoformat()
                _save_state(self._state_path, self._state)
        else:
            logger.info("PerformanceReporter: daily already sent for %s — skipping", today)

        # --- Weekly (every Friday) ---
        if today.weekday() == 4:
            if not self.was_weekly_sent_for_isoweek(today):
                week_start = date.fromisocalendar(today.isocalendar()[0],
                                                  today.isocalendar()[1], 1)
                week_trades = _read_trades(self._log_dir, since=week_start)
                week_eq_start = self._state.get("week_equity_start", equity_start_of_day)
                sent_ok = self._send_weekly(today, week_trades, float(week_eq_start), equity_end)
                if sent_ok:
                    # Mark sent + advance equity baseline only on success.
                    # Failure leaves baseline unchanged so next attempt
                    # still computes the correct period return.
                    self._state["last_weekly_report"] = self._isoweek_key(today)
                    self._state["week_equity_start"] = equity_end
                    _save_state(self._state_path, self._state)
            else:
                logger.info(
                    "PerformanceReporter: weekly already sent for %s — skipping",
                    self._isoweek_key(today),
                )

        # Track Monday as start of new week's equity baseline
        if today.weekday() == 0:
            self._state["week_equity_start"] = equity_start_of_day
            _save_state(self._state_path, self._state)

        # --- Monthly (last trading day of month) ---
        if _is_last_trading_day_of_month(today):
            if not self.was_monthly_sent_for_month(today):
                month_start = today.replace(day=1)
                month_trades = _read_trades(self._log_dir, since=month_start)
                month_eq_start = self._state.get("month_equity_start", self._initial_equity)
                sent_ok = self._send_monthly(today, month_trades, float(month_eq_start), equity_end)
                if sent_ok:
                    self._state["last_monthly_report"] = self._month_key(today)
                    self._state["month_equity_start"] = equity_end
                    _save_state(self._state_path, self._state)
            else:
                logger.info(
                    "PerformanceReporter: monthly already sent for %s — skipping",
                    self._month_key(today),
                )

        if today.day == 1:
            self._state["month_equity_start"] = equity_start_of_day
            _save_state(self._state_path, self._state)

        # --- 8-week review (every 8th Friday from launch) ---
        launch_str = self._state.get("launch_date", today.isoformat())
        launch_date = date.fromisoformat(launch_str)
        days_since_launch = (today - launch_date).days
        last_8wk = self._state.get("last_8week_report", "")
        if (
            today.weekday() == 4
            and days_since_launch > 0
            and days_since_launch % 56 < 7  # within 7 days of an 8-week multiple
            and last_8wk != today.isoformat()
        ):
            period_start = date.fromisoformat(
                self._state.get("8week_equity_start_date", launch_str)
            )
            period_trades = _read_trades(self._log_dir, since=period_start)
            period_eq_start = self._state.get("8week_equity_start",
                                              self._initial_equity)
            self._send_8week(today, period_trades, float(period_eq_start),
                             equity_end, float(self._initial_equity or equity_end))
            self._state["last_8week_report"] = today.isoformat()
            self._state["8week_equity_start"] = equity_end
            self._state["8week_equity_start_date"] = today.isoformat()
            _save_state(self._state_path, self._state)

    def on_circuit_breaker(
        self,
        reason: str,
        equity: float,
        timestamp: datetime,
    ) -> None:
        """Call immediately when the circuit breaker fires."""
        if not self._enabled or not self._to_address:
            return
        recent_trades = _read_trades(self._log_dir)[-10:]
        body = _build_circuit_breaker_body(reason, equity, timestamp, recent_trades)
        mode = "PAPER" if self._is_paper else "LIVE"
        html = _html_wrap(
            title=f"🚨 Circuit Breaker — {reason}",
            period_label=f"⚠️ ALERT [{mode}]",
            body=body,
            is_paper=self._is_paper,
        )
        self._send(
            subject=f"🚨 [ML Trader {mode}] Circuit Breaker — {reason}",
            html=html,
        )

    # ------------------------------------------------------------------
    # Private: per-period senders
    # ------------------------------------------------------------------

    def _send_daily(
        self,
        today: date,
        trades: list[dict],
        eq_start: float,
        eq_end: float,
        engine_state: Optional[dict] = None,
    ) -> bool:
        """Returns True iff the email was successfully delivered."""
        stats = _compute_stats(trades)
        body = _build_performance_body(
            "DAILY", stats, eq_start, eq_end,
            f"{today.strftime('%A, %d %b %Y')}",
        )
        if engine_state:
            body += _build_engine_state_section(engine_state)
        mode = "PAPER" if self._is_paper else "LIVE"
        html = _html_wrap(
            title=f"Daily Report {today}",
            period_label="DAILY",
            body=body,
            is_paper=self._is_paper,
        )
        return self._send(
            subject=f"[ML Trader {mode}] Daily Report — {today.strftime('%d %b %Y')}",
            html=html,
        )

    def _send_weekly(
        self,
        friday: date,
        trades: list[dict],
        eq_start: float,
        eq_end: float,
    ) -> bool:
        """Returns True iff the email was successfully delivered."""
        week_start = date.fromisocalendar(
            friday.isocalendar()[0], friday.isocalendar()[1], 1
        )
        stats = _compute_stats(trades)
        body = _build_performance_body(
            "WEEKLY", stats, eq_start, eq_end,
            f"{week_start.strftime('%d %b')} – {friday.strftime('%d %b %Y')}",
        )
        mode = "PAPER" if self._is_paper else "LIVE"
        html = _html_wrap(
            title=f"Weekly Report W{friday.isocalendar()[1]}",
            period_label="WEEKLY REVIEW",
            body=body,
            is_paper=self._is_paper,
        )
        return self._send(
            subject=(
                f"[ML Trader {mode}] Weekly Report — "
                f"W{friday.isocalendar()[1]} {friday.year}"
            ),
            html=html,
        )

    def _send_monthly(
        self,
        last_day: date,
        trades: list[dict],
        eq_start: float,
        eq_end: float,
    ) -> bool:
        """Returns True iff the email was successfully delivered."""
        stats = _compute_stats(trades)
        body = _build_performance_body(
            "MONTHLY", stats, eq_start, eq_end,
            last_day.strftime("%B %Y"),
        )
        mode = "PAPER" if self._is_paper else "LIVE"
        html = _html_wrap(
            title=f"Monthly Report {last_day.strftime('%B %Y')}",
            period_label="MONTHLY REVIEW",
            body=body,
            is_paper=self._is_paper,
        )
        return self._send(
            subject=(
                f"[ML Trader {mode}] Monthly Report — "
                f"{last_day.strftime('%B %Y')}"
            ),
            html=html,
        )

    def _send_8week(
        self,
        friday: date,
        trades: list[dict],
        eq_period_start: float,
        eq_end: float,
        eq_all_time_start: float,
    ) -> None:
        stats = _compute_stats(trades)
        all_time_return = (eq_end - eq_all_time_start) / eq_all_time_start if eq_all_time_start else 0.0
        # Inject all-time return as a note at the top of the body
        note = (
            f'<div style="background:#1a237e;border-radius:6px;padding:12px 14px;'
            f'margin-bottom:18px;font-size:13px;color:#90caf9;">'
            f'📊 <strong>All-time return since launch:</strong> '
            f'<span style="color:{_pct_color(all_time_return)};font-weight:700;">'
            f'{_fmt_pct(all_time_return)}</span>'
            f' &nbsp;|&nbsp; Start: {_fmt_dollar(eq_all_time_start)}'
            f' → Current: {_fmt_dollar(eq_end)}'
            f'</div>'
        )
        period_start = friday - __import__("datetime").timedelta(weeks=8)
        body = note + _build_performance_body(
            "8-WEEK", stats, eq_period_start, eq_end,
            f"{period_start.strftime('%d %b')} – {friday.strftime('%d %b %Y')}",
        )
        mode = "PAPER" if self._is_paper else "LIVE"
        html = _html_wrap(
            title="8-Week Paper Trading Review",
            period_label="8-WEEK REVIEW",
            body=body,
            is_paper=self._is_paper,
        )
        self._send(
            subject=f"[ML Trader {mode}] 8-Week Review — {friday.strftime('%d %b %Y')}",
            html=html,
        )

    # ------------------------------------------------------------------
    # Private: SMTP delivery
    # ------------------------------------------------------------------

    def _send(self, subject: str, html: str) -> bool:
        """Returns True iff the email was successfully delivered."""
        if not self._smtp_user or not self._smtp_password:
            logger.warning(
                "PerformanceReporter: SMTP_USER or SMTP_PASSWORD not set — "
                "email skipped. Add these to your .env file."
            )
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self._smtp_from
            msg["To"] = self._to_address
            # Plain text fallback
            plain = (
                f"{subject}\n\n"
                "This email requires an HTML-capable email client.\n"
                "Please view it in Gmail, Outlook, or Apple Mail."
            )
            msg.attach(MIMEText(plain, "plain", "utf-8"))
            msg.attach(MIMEText(html, "html", "utf-8"))

            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=15) as smtp:
                smtp.ehlo()
                if self._smtp_port == 587:
                    smtp.starttls()
                smtp.login(self._smtp_user, self._smtp_password)
                smtp.sendmail(self._smtp_from, [self._to_address], msg.as_string())

            logger.info("PerformanceReporter: sent '%s' → %s", subject, self._to_address)
            return True
        except Exception:
            logger.error(
                "PerformanceReporter: email delivery failed for '%s':\n%s",
                subject, traceback.format_exc(),
            )
            return False
