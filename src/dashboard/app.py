"""ML Trader — Diablo v1 — Live Trading Dashboard.

Reads shared_state.json written by StructuredLogger.update_shared_state().
No direct access to broker, HMM, or order engine.

Run:
    streamlit run src/dashboard/app.py
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Page config — MUST be first Streamlit call
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="🧠 ML Trader — Diablo v1",
    page_icon="🧠",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Color palette (from GUI style spec)
# ---------------------------------------------------------------------------

PRIMARY_BG = "#0e1117"
CARD_BG = "#161b2e"
GRID_COLOR = "#1f2235"
ACCENT_BLUE = "#2196F3"
ACCENT_ORANGE = "#FF9800"
ACCENT_GREEN = "#00E676"
NEUTRAL_GREY = "#546E7A"
METRIC_LABEL_COLOR = "#9aa0b4"
TEXT_WHITE = "#FFFFFF"

_REGIME_COLORS: dict[str, str] = {
    "TRENDING_UP": ACCENT_GREEN,
    "TRENDING_DOWN": "#EF5350",
    "BREAKOUT": ACCENT_ORANGE,
    "SQUEEZE": "#80DEEA",
    "CHOPPY": "#CE93D8",
    "UNKNOWN": NEUTRAL_GREY,
}

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
        /* ═══════════════════════════════════════════════
           BASE
        ═══════════════════════════════════════════════ */
        .block-container { padding-top: 1.8rem; }

        /* ── Metric cards ── */
        [data-testid="stMetricValue"] { font-size: 1.35rem; font-weight: 700; }
        [data-testid="stMetricLabel"] {
            font-size: 0.80rem; color: #9aa0b4;
            text-transform: uppercase; letter-spacing: 0.04em;
        }
        div[data-testid="metric-container"] {
            background: linear-gradient(160deg, #1a2040 0%, #161b2e 100%);
            border: 1px solid #1f2235;
            border-top: 2px solid #2196F3;
            border-radius: 10px; padding: 14px 18px; margin-bottom: 8px;
            transition: box-shadow 0.2s ease;
        }
        div[data-testid="metric-container"]:hover {
            box-shadow: 0 4px 22px rgba(33,150,243,0.14);
        }

        /* ── Primary button (gradient blue, lift on hover) ── */
        [data-testid="baseButton-primary"] {
            background: linear-gradient(135deg, #1565C0 0%, #2196F3 100%) !important;
            border: none !important;
            border-radius: 8px !important;
            color: #fff !important;
            font-weight: 600 !important;
            letter-spacing: 0.04em !important;
            box-shadow: 0 2px 10px rgba(33,150,243,0.30) !important;
            transition: transform 0.15s ease, box-shadow 0.15s ease !important;
        }
        [data-testid="baseButton-primary"]:hover:not(:disabled) {
            transform: translateY(-1px) !important;
            box-shadow: 0 6px 22px rgba(33,150,243,0.50) !important;
        }
        [data-testid="baseButton-primary"]:active:not(:disabled) {
            transform: translateY(0px) !important;
        }
        [data-testid="baseButton-primary"]:disabled {
            opacity: 0.35 !important;
            cursor: not-allowed !important;
        }

        /* ── Secondary button (outlined, blue border on hover) ── */
        [data-testid="baseButton-secondary"] {
            border: 1px solid #2a3050 !important;
            border-radius: 8px !important;
            background: transparent !important;
            color: #9aa0b4 !important;
            transition: border-color 0.15s ease, color 0.15s ease,
                        background 0.15s ease !important;
        }
        [data-testid="baseButton-secondary"]:hover {
            border-color: #2196F3 !important;
            color: #e0e6ff !important;
            background: rgba(33,150,243,0.07) !important;
        }

        /* ── Live status pulse dot ── */
        @keyframes pulse-dot {
            0%, 100% { opacity: 1; }
            50%       { opacity: 0.35; }
        }
        .dot-live { animation: pulse-dot 2.2s ease-in-out infinite; display: inline-block; }

        /* ── Expanders (positions / trades tables) ── */
        [data-testid="stExpander"] {
            border: 1px solid #1f2235 !important;
            border-radius: 10px !important;
            overflow: hidden !important;
        }
        details > summary {
            font-weight: 600 !important;
            font-size: 0.95rem !important;
            padding: 12px 16px !important;
        }

        /* ── Plotly chart toolbar ── */
        .modebar { background-color: transparent !important; }
        .modebar-btn path { fill: rgba(154,160,180,0.40) !important; }
        .modebar-btn:hover path { fill: #2196F3 !important; }

        /* ── Sidebar ── */
        [data-testid="stSidebar"] { border-right: 1px solid #1f2235; }
        [data-testid="stSidebar"] .stMarkdown p { font-size: 0.87rem; }

        /* ── Hide Streamlit's auto-generated multipage nav (we use st.page_link instead) ── */
        [data-testid="stSidebarNav"] { display: none !important; }

        /* ── Custom nav link pills ── */
        [data-testid="stPageLink"] a {
            border-radius: 8px !important;
            padding: 6px 12px !important;
            font-weight: 600 !important;
            transition: background 0.15s ease !important;
        }
        [data-testid="stPageLink"] a:hover {
            background: rgba(33,150,243,0.10) !important;
        }
        [data-testid="stPageLink"][aria-selected="true"] a,
        [data-testid="stPageLink"] a[aria-selected="true"] {
            background: rgba(33,150,243,0.15) !important;
            border-left: 3px solid #2196F3 !important;
        }

        /* ── Market clock pills ── */
        .mkt-open {
            display: inline-block;
            background: rgba(0,230,118,0.10); border: 1px solid #00E676;
            color: #00E676; border-radius: 20px;
            padding: 5px 18px; font-weight: 700; font-size: 1rem;
        }
        .mkt-closed {
            display: inline-block;
            background: rgba(239,83,80,0.10); border: 1px solid #EF5350;
            color: #EF5350; border-radius: 20px;
            padding: 5px 18px; font-weight: 700; font-size: 1rem;
        }
        .clock-card {
            background: linear-gradient(160deg,#1a2040 0%,#161b2e 100%);
            border: 1px solid #1f2235; border-radius: 10px;
            padding: 12px 16px; margin-bottom: 0.6rem; text-align: center;
        }
        .clock-label { font-size: 0.75rem; color: #9aa0b4; text-transform: uppercase; letter-spacing: 0.05em; }
        .clock-time  { font-size: 1.25rem; font-weight: 700; color: #e0e6ff; }
        .clock-sub   { font-size: 0.82rem; color: #9aa0b4; margin-top: 2px; }
        .engine-mode { font-size: 0.82rem; font-weight: 600; margin-top: 4px; }

        /* ═══════════════════════════════════════════════
           MOBILE  ≤ 768 px  (iPhone / small Android)
        ═══════════════════════════════════════════════ */
        @media screen and (max-width: 768px) {
            .block-container {
                padding-left: 0.6rem !important;
                padding-right: 0.6rem !important;
                padding-top: 0.8rem !important;
            }
            [data-testid="stHorizontalBlock"] {
                flex-direction: column !important; gap: 0 !important;
            }
            [data-testid="column"] {
                width: 100% !important; flex: 1 1 100% !important; min-width: 0 !important;
            }
            [data-testid="stMetricValue"] { font-size: 1.55rem !important; }
            div[data-testid="metric-container"] {
                padding: 12px 14px !important; margin-bottom: 6px !important;
            }
            h1 { font-size: 1.3rem !important; }
            h2, h3 { font-size: 1rem !important; }
            [data-testid="baseButton-primary"],
            [data-testid="baseButton-secondary"] {
                min-height: 44px !important; font-size: 1rem !important;
            }
            [data-testid="stDataFrame"] { overflow-x: auto !important; }
        }

        /* ═══════════════════════════════════════════════
           TABLET PORTRAIT  769 – 1024 px  (iPad mini/Air)
        ═══════════════════════════════════════════════ */
        @media screen and (min-width: 769px) and (max-width: 1024px) {
            .block-container {
                padding-left: 1rem !important; padding-right: 1rem !important;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dark_layout() -> dict:
    """Return Plotly layout kwargs for consistent dark theme."""
    return dict(
        plot_bgcolor=PRIMARY_BG,
        paper_bgcolor=PRIMARY_BG,
        font=dict(color=TEXT_WHITE, family="Inter, sans-serif"),
        title_font=dict(size=15, color=TEXT_WHITE),
        xaxis=dict(
            gridcolor=GRID_COLOR,
            showgrid=True,
            zeroline=False,
            linecolor=GRID_COLOR,
        ),
        yaxis=dict(
            gridcolor=GRID_COLOR,
            showgrid=True,
            zeroline=False,
            linecolor=GRID_COLOR,
        ),
        legend=dict(
            bgcolor="rgba(22,27,46,0.9)",
            bordercolor=GRID_COLOR,
            borderwidth=1,
            x=0.01,
            y=0.98,
        ),
        margin=dict(l=40, r=16, t=48, b=40),
    )


def _load_config() -> dict:
    """Load settings.yaml relative to project root."""
    try:
        import yaml  # type: ignore

        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "settings.yaml"
        )
        with open(os.path.normpath(config_path), encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _load_shared_state(path: str) -> Optional[dict]:
    """Load and parse shared_state.json. Returns None on any error."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _state_age_seconds(state: dict) -> float:
    """Return seconds since shared_state was last written."""
    try:
        ts_str = state.get("timestamp", "")
        if not ts_str:
            return float("inf")
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - ts).total_seconds()
    except Exception:
        return float("inf")


def _fmt_dollar(v: float) -> str:
    try:
        return f"${v:,.2f}"
    except Exception:
        return "—"


def _fmt_pct(v: float) -> str:
    try:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v * 100:.2f}%"
    except Exception:
        return "—"


def _load_trades_log(log_dir: str) -> list[dict]:
    """Read today's trades from trades.log (JSON Lines)."""
    trades = []
    path = os.path.join(log_dir, "trades.log")
    try:
        today = datetime.now().date().isoformat()
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("ts", "").startswith(today):
                    trades.append(rec)
    except Exception:
        pass
    return trades


def _get_market_status() -> dict:
    """Return NYSE open/closed status, next event time, and SA/ET clocks."""
    try:
        import exchange_calendars as xcals
        import pandas as pd
        import pytz as _pytz

        et_tz = _pytz.timezone("America/New_York")
        sast_tz = _pytz.timezone("Africa/Johannesburg")
        now_utc = datetime.now(timezone.utc)
        now_et = now_utc.astimezone(et_tz)
        now_sast = now_utc.astimezone(sast_tz)

        cal = xcals.get_calendar("NYSE")
        today_ts = pd.Timestamp(now_et.date())

        if cal.is_session(today_ts):
            open_utc = cal.session_open(today_ts).to_pydatetime().replace(tzinfo=timezone.utc)
            close_utc = cal.session_close(today_ts).to_pydatetime().replace(tzinfo=timezone.utc)
            if open_utc <= now_utc < close_utc:
                secs = int((close_utc - now_utc).total_seconds())
                return {
                    "is_open": True, "now_et": now_et, "now_sast": now_sast,
                    "event_label": "Closes", "secs_to_event": secs,
                    "event_et": close_utc.astimezone(et_tz),
                    "event_sast": close_utc.astimezone(sast_tz),
                }

        # Market closed — find next session
        future = cal.sessions[cal.sessions > today_ts]
        if not len(future):
            return {"is_open": False, "now_et": now_et, "now_sast": now_sast}
        next_open_utc = cal.session_open(future[0]).to_pydatetime().replace(tzinfo=timezone.utc)
        secs = int((next_open_utc - now_utc).total_seconds())
        return {
            "is_open": False, "now_et": now_et, "now_sast": now_sast,
            "event_label": "Opens", "secs_to_event": secs,
            "event_et": next_open_utc.astimezone(et_tz),
            "event_sast": next_open_utc.astimezone(sast_tz),
        }
    except Exception:
        return {"is_open": False, "now_et": None, "now_sast": None}


def _fmt_countdown(secs: int) -> str:
    if secs <= 0:
        return "now"
    h, rem = divmod(secs, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m" if h > 0 else f"{m}m"


def _engine_mode(live: bool, ms: dict, r_info: dict) -> tuple[str, str]:
    """Return (label, hex-colour) for the current engine operating mode."""
    if not live:
        return "Offline", "#546E7A"
    if not ms.get("is_open"):
        return "Waiting — Market Closed", "#FF9800"
    trained = any(
        isinstance(v, dict) and v.get("regime", "UNKNOWN") != "UNKNOWN"
        for v in r_info.values()
    )
    return ("Auto-Trading  ✦  Active", "#00E676") if trained else ("Accumulating Training Data", "#FF9800")


# ---------------------------------------------------------------------------
# Config loading (cached for session)
# ---------------------------------------------------------------------------

if "cfg" not in st.session_state:
    st.session_state["cfg"] = _load_config()

cfg: dict = st.session_state["cfg"]
mon_cfg: dict = cfg.get("monitoring", {})
shared_state_path: str = mon_cfg.get("shared_state_file", "shared_state.json")
log_dir: str = mon_cfg.get("log_dir", "logs")
refresh_seconds: int = int(mon_cfg.get("dashboard_refresh_seconds", 5))

# ---------------------------------------------------------------------------
# Auto-refresh: re-run every N seconds using query param trick
# ---------------------------------------------------------------------------

try:
    from streamlit_autorefresh import st_autorefresh  # type: ignore

    st_autorefresh(interval=refresh_seconds * 1000, key="autorefresh")
except ImportError:
    # Fallback: manual refresh button in sidebar
    pass

# ---------------------------------------------------------------------------
# Load shared state
# ---------------------------------------------------------------------------

state: Optional[dict] = _load_shared_state(shared_state_path)
age_seconds: float = _state_age_seconds(state) if state else float("inf")
engine_live: bool = state is not None and age_seconds <= 30

# Market status + regime info (used by clock and engine-mode badge)
_ms = _get_market_status()
_regime_info: dict = state.get("regime_info", {}) if state else {}
_mode_label, _mode_color = _engine_mode(engine_live, _ms, _regime_info)

# ---------------------------------------------------------------------------
# ⚙️ SIDEBAR
# ---------------------------------------------------------------------------

# Custom multipage navigation (auto-nav hidden via CSS above)
with st.sidebar:
    st.page_link("app.py", label="Dashboard", icon="📊")
    st.page_link("pages/1_Settings.py", label="Settings", icon="⚙️")

st.sidebar.markdown("---")
st.sidebar.header("Configuration")

# Paper vs Live mode
alpaca_url: str = os.environ.get("ALPACA_BASE_URL", "")
is_live = "paper" not in alpaca_url.lower() and bool(alpaca_url)
if is_live:
    st.sidebar.markdown(
        '<span style="background:#EF5350;color:#fff;padding:3px 10px;'
        'border-radius:4px;font-weight:700;font-size:0.85rem;">🔴 LIVE MODE</span>',
        unsafe_allow_html=True,
    )
else:
    st.sidebar.markdown(
        '<span style="background:#546E7A;color:#fff;padding:3px 10px;'
        'border-radius:4px;font-weight:700;font-size:0.85rem;">📋 PAPER MODE</span>',
        unsafe_allow_html=True,
    )

st.sidebar.markdown("---")

# Assets
assets_cfg = cfg.get("assets", {})
equity_assets = assets_cfg.get("primary_equity", ["SPY", "QQQ"])
crypto_assets = assets_cfg.get("primary_crypto", ["BTC/USD", "ETH/USD"])
all_assets = equity_assets + crypto_assets
st.sidebar.markdown(f"**Assets:** {', '.join(all_assets)}")

# Clock (SAST · ET · NYSE status)
_sb_sast = _ms.get("now_sast")
_sb_et   = _ms.get("now_et")
if _sb_sast and _sb_et:
    _nyse_status = "🟢 Open" if _ms.get("is_open") else "🔴 Closed"
    st.sidebar.markdown(
        f"🕐 **{_sb_sast.strftime('%H:%M')} SAST** · {_sb_et.strftime('%H:%M')} ET  \n"
        f"NYSE {_nyse_status}"
    )

# Confidence thresholds
hmm_conf = cfg.get("hmm", {}).get("confidence_threshold", 0.55)
lgbm_conf = cfg.get("lgbm", {}).get("confidence_threshold", 0.75)
st.sidebar.markdown(f"**HMM Confidence:** {hmm_conf:.2f}")
st.sidebar.markdown(f"**LGBM Confidence:** {lgbm_conf:.2f}")

st.sidebar.markdown("---")

# Engine status / Run button
if engine_live:
    st.sidebar.success(f"✅ Engine running  ({age_seconds:.0f}s ago)")
else:
    st.sidebar.button(
        "🚀 Run Live Trading",
        use_container_width=True,
        type="primary",
        help="Start the trading engine from the terminal: python main.py",
        disabled=True,
    )
    st.sidebar.caption("Start engine from terminal: `python main.py`")

st.sidebar.markdown("---")

# 🛡️ Risk Limits expander
with st.sidebar.expander("🛡️ Risk Limits", expanded=False):
    risk_cfg = cfg.get("risk", {})
    st.write(f"Max DD (daily): {risk_cfg.get('daily_dd_limit', 0.03)*100:.1f}%")
    st.write(f"Max DD (30-min): {risk_cfg.get('half_hour_dd_limit', 0.01)*100:.1f}%")
    st.write(
        f"Consec. loss pause: {risk_cfg.get('consecutive_loss_pause', 3)} trades "
        f"→ {risk_cfg.get('pause_duration_minutes', 120)} min"
    )
    st.write(f"Max positions: {risk_cfg.get('max_simultaneous_positions', 2)}")
    st.write(f"Max leverage: {risk_cfg.get('max_portfolio_leverage', 1.25):.2f}×")

    if state:
        cb_active = state.get("circuit_breaker_active", False)
        cb_label = "🔴 ACTIVE" if cb_active else "🟢 OK"
        st.write(f"Circuit Breaker: {cb_label}")

    # PDT info from regime_info if present
    regime_info = state.get("regime_info", {}) if state else {}
    pdt_count = regime_info.get("pdt_count")
    pdt_max = cfg.get("pdt", {}).get("max_daytrades_per_5d", 999)
    if pdt_count is not None:
        # PDT rule eliminated April 2026; guard is informational counter only
        pdt_display = "∞" if pdt_max >= 999 else str(pdt_max)
        st.write(f"Day trades (5d): {pdt_count}/{pdt_display}")

# ---------------------------------------------------------------------------
# MAIN AREA — title
# ---------------------------------------------------------------------------

st.markdown(
    "<h1 style='text-align:center;letter-spacing:-0.01em;'>🧠 ML Trader — Diablo v1</h1>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Market Clock  (NYSE status · countdown · SAST / ET clocks)
# ---------------------------------------------------------------------------

mc1, mc2, mc3 = st.columns(3, gap="medium")

with mc1:
    mkt_pill = (
        '<span class="mkt-open">🟢 NYSE OPEN</span>'
        if _ms.get("is_open")
        else '<span class="mkt-closed">🔴 NYSE CLOSED</span>'
    )
    event_label = _ms.get("event_label", "")
    event_et   = _ms.get("event_et")
    event_sast = _ms.get("event_sast")
    countdown  = _fmt_countdown(_ms.get("secs_to_event", 0))
    event_line = (
        f"<div class='clock-sub'>{event_label} "
        f"{event_et.strftime('%H:%M ET')} · {event_sast.strftime('%H:%M SAST')}"
        f"&nbsp;&nbsp;⏱ {countdown}</div>"
        if event_et else ""
    )
    st.markdown(
        f"<div class='clock-card'>{mkt_pill}{event_line}</div>",
        unsafe_allow_html=True,
    )

with mc2:
    now_sast = _ms.get("now_sast")
    now_et   = _ms.get("now_et")
    if now_sast and now_et:
        st.markdown(
            f"<div class='clock-card'>"
            f"<div class='clock-label'>South Africa</div>"
            f"<div class='clock-time'>{now_sast.strftime('%H:%M')}"
            f"<span style='font-size:0.7rem;color:#546E7A;'> SAST</span></div>"
            f"<div class='clock-sub'>New York {now_et.strftime('%H:%M ET')}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown("<div class='clock-card'><div class='clock-sub'>—</div></div>",
                    unsafe_allow_html=True)

with mc3:
    dot = '<span class="dot-live">●</span>' if engine_live else '<span style="color:#546E7A;">●</span>'
    mode_html = (
        f"<div class='clock-label'>Engine</div>"
        f"<div style='font-size:0.95rem;font-weight:700;color:#e0e6ff;margin:4px 0;'>"
        f"{dot} {'LIVE' if engine_live else 'OFFLINE'}</div>"
        f"<div class='engine-mode' style='color:{_mode_color};'>{_mode_label}</div>"
    )
    st.markdown(f"<div class='clock-card'>{mode_html}</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Section 1 — Status bar (last update · open positions)
# ---------------------------------------------------------------------------

status_placeholder = st.empty()

with status_placeholder.container():
    s1, s2 = st.columns([3, 3], gap="large")
    with s1:
        if state:
            ts_raw = state.get("timestamp", "")
            try:
                ts_dt = datetime.fromisoformat(ts_raw)
                ts_fmt = ts_dt.strftime("%H:%M:%S UTC")
            except Exception:
                ts_fmt = ts_raw[:19]
            st.markdown(f"**Last update:** {ts_fmt}")
        else:
            st.markdown("**Last update:** —")

    with s2:
        if state:
            positions = state.get("positions", {})
            n_pos = len(positions)
            st.markdown(
                f"**Open Positions:** {n_pos} &nbsp;|&nbsp; "
                f"**Session:** {'Active' if engine_live else 'Idle'}"
            )
        else:
            st.markdown("**Session:** Waiting for engine …")

# Circuit breaker warning banner
if state and state.get("circuit_breaker_active"):
    st.error(
        "🚨 **CIRCUIT BREAKER ACTIVE** — Trading halted. Check logs for reason.",
        icon=None,
    )

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 2 — 📊 KPI Metric Cards
# ---------------------------------------------------------------------------

st.subheader("📊 Performance")

equity = state.get("equity", 0.0) if state else None
cash = state.get("cash", 0.0) if state else None
daily_pnl_raw = state.get("daily_pnl", 0.0) if state else None
positions = state.get("positions", {}) if state else {}
regime_info = state.get("regime_info", {}) if state else {}

# Load today's completed trades from trades.log
today_trades = _load_trades_log(log_dir)

# Compute session win rate
if today_trades:
    wins = sum(1 for t in today_trades if t.get("pnl_pct", 0) > 0)
    session_wr = wins / len(today_trades)
    avg_hold = sum(t.get("hold_bars", 0) for t in today_trades) / len(today_trades)
else:
    session_wr = None
    avg_hold = None

# Row 1: Equity | Daily P&L
r1c1, r1c2 = st.columns(2, gap="large")
with r1c1:
    equity_display = _fmt_dollar(equity) if equity is not None else "—"
    st.metric(
        label="Portfolio Equity (USD)",
        value=equity_display,
    )
with r1c2:
    if daily_pnl_raw is not None and equity and equity > 0:
        daily_pnl_pct = daily_pnl_raw / equity
        delta_str = _fmt_pct(daily_pnl_pct)
        st.metric(
            label="Daily P&L",
            value=_fmt_dollar(daily_pnl_raw),
            delta=delta_str,
        )
    else:
        st.metric(label="Daily P&L", value="—")

# Row 2: Open Positions | Today's Trades
r2c1, r2c2 = st.columns(2, gap="large")
with r2c1:
    st.metric(label="Open Positions", value=str(len(positions)))
with r2c2:
    st.metric(label="Today's Trades", value=str(len(today_trades)))

# Row 3: Win Rate | Avg Trade Duration
r3c1, r3c2 = st.columns(2, gap="large")
with r3c1:
    wr_display = f"{session_wr*100:.1f}%" if session_wr is not None else "—"
    st.metric(label="Win Rate (session)", value=wr_display)
with r3c2:
    hold_display = f"{avg_hold:.1f} bars" if avg_hold is not None else "—"
    st.metric(label="Avg Trade Duration", value=hold_display)

# Row 4: HMM Regime | LGBM Confidence (per asset)
r4c1, r4c2 = st.columns(2, gap="large")
with r4c1:
    if regime_info:
        regime_lines = []
        for asset, info in regime_info.items():
            regime_val = info.get("regime", "UNKNOWN") if isinstance(info, dict) else str(info)
            color = _REGIME_COLORS.get(regime_val, NEUTRAL_GREY)
            regime_lines.append(
                f'<span style="color:{color};font-weight:700;">{asset}: {regime_val}</span>'
            )
        st.markdown(
            "**HMM Regime**<br>" + "<br>".join(regime_lines),
            unsafe_allow_html=True,
        )
    else:
        st.metric(label="HMM Regime", value="—")

with r4c2:
    if regime_info:
        conf_lines = []
        for asset, info in regime_info.items():
            conf_val = info.get("confidence", None) if isinstance(info, dict) else None
            conf_str = f"{conf_val:.3f}" if conf_val is not None else "—"
            conf_lines.append(f"{asset}: {conf_str}")
        st.markdown(
            "**LGBM Confidence**<br>" + "<br>".join(conf_lines),
            unsafe_allow_html=True,
        )
    else:
        st.metric(label="LGBM Confidence", value="—")

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 3 — 📈 Equity Curve Chart
# ---------------------------------------------------------------------------

st.subheader("📈 Equity Curve — Last 30 min")

equity_curve_30m = state.get("equity_curve_30m", []) if state else []

fig_equity = go.Figure()

if equity_curve_30m:
    try:
        timestamps = [pt[0] for pt in equity_curve_30m]
        equities_vals = [float(pt[1]) for pt in equity_curve_30m]
        fig_equity.add_trace(
            go.Scatter(
                x=timestamps,
                y=equities_vals,
                mode="lines+markers",
                name="Equity",
                line=dict(color=ACCENT_ORANGE, width=2.5),
                marker=dict(size=6, color=ACCENT_ORANGE),
            )
        )
    except Exception:
        pass

fig_equity.update_layout(
    title="Portfolio Equity",
    xaxis_title="Time",
    yaxis_title="Equity (USD)",
    height=340,
    **_dark_layout(),
)
st.plotly_chart(fig_equity, use_container_width=True)

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 4 — 🧠 Regime / Signal Timeline Chart
# ---------------------------------------------------------------------------

st.subheader("🧠 Regime & Signals")

last_10_signals = state.get("last_10_signals", []) if state else []

fig_regime = go.Figure()

# Plot regime colored background bands per asset using signals
if last_10_signals:
    try:
        for asset in all_assets:
            asset_signals = [s for s in last_10_signals if s.get("asset") == asset]
            if not asset_signals:
                continue

            timestamps_sig = []
            directions = []
            labels = []

            for sig in asset_signals:
                ts_raw_sig = sig.get("ts", "")
                direction = sig.get("direction", "")
                reason = sig.get("reason", "")

                timestamps_sig.append(ts_raw_sig)
                directions.append(direction)
                labels.append(f"{asset} {direction} ({reason})")

            # Plot long signals as blue triangles up, short as red triangles down
            long_ts = [timestamps_sig[i] for i, d in enumerate(directions) if d == "LONG"]
            short_ts = [timestamps_sig[i] for i, d in enumerate(directions) if d == "SHORT"]

            if long_ts:
                fig_regime.add_trace(
                    go.Scatter(
                        x=long_ts,
                        y=[asset] * len(long_ts),
                        mode="markers",
                        name=f"{asset} LONG",
                        marker=dict(
                            symbol="triangle-up",
                            size=14,
                            color=ACCENT_BLUE,
                        ),
                        hovertext=[
                            labels[i]
                            for i, d in enumerate(directions)
                            if d == "LONG"
                        ],
                        hoverinfo="text+x",
                    )
                )
            if short_ts:
                fig_regime.add_trace(
                    go.Scatter(
                        x=short_ts,
                        y=[asset] * len(short_ts),
                        mode="markers",
                        name=f"{asset} SHORT",
                        marker=dict(
                            symbol="triangle-down",
                            size=14,
                            color="#EF5350",
                        ),
                        hovertext=[
                            labels[i]
                            for i, d in enumerate(directions)
                            if d == "SHORT"
                        ],
                        hoverinfo="text+x",
                    )
                )

    except Exception:
        pass

# Regime colored bands from regime_info
if regime_info:
    for asset, info in regime_info.items():
        if isinstance(info, dict):
            regime_val = info.get("regime", "UNKNOWN")
        else:
            regime_val = str(info)
        color = _REGIME_COLORS.get(regime_val, NEUTRAL_GREY)
        fig_regime.add_annotation(
            text=f"<b>{asset}</b>: {regime_val}",
            xref="paper",
            yref="paper",
            x=0.01,
            y=0.99 - list(regime_info.keys()).index(asset) * 0.12,
            showarrow=False,
            font=dict(color=color, size=12),
            bgcolor="rgba(22,27,46,0.7)",
        )

if not last_10_signals and not regime_info:
    fig_regime.add_annotation(
        text="No signal history available",
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(color=METRIC_LABEL_COLOR, size=14),
    )

fig_regime.update_layout(
    title="Last 10 Signals by Asset",
    xaxis_title="Time",
    yaxis_title="Asset",
    height=320,
    **_dark_layout(),
)
st.plotly_chart(fig_regime, use_container_width=True)

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 5 — 📅 Open Positions Table
# ---------------------------------------------------------------------------

with st.expander("📅 Open Positions", expanded=True):
    if positions:
        rows = []
        for symbol, pos in positions.items():
            try:
                direction = pos.get("direction", "—")
                entry_price = pos.get("entry_price", 0.0)
                current_price = pos.get("current_price", 0.0)
                unr_pnl_pct = pos.get("unrealised_pnl_pct", 0.0)
                stop_price = pos.get("stop_price")
                tp_price = pos.get("take_profit_price")
                bars_held = pos.get("bars_held", 0)

                rows.append(
                    {
                        "Asset": symbol,
                        "Direction": direction,
                        "Entry Price": f"{entry_price:.4f}",
                        "Current P&L %": f"{_fmt_pct(unr_pnl_pct)}",
                        "Stop": f"{stop_price:.4f}" if stop_price else "—",
                        "TP": f"{tp_price:.4f}" if tp_price else "—",
                        "Bars Held": bars_held,
                    }
                )
            except Exception:
                pass

        if rows:
            import pandas as pd

            df_pos = pd.DataFrame(rows)
            st.dataframe(df_pos, use_container_width=True, hide_index=True)
        else:
            st.info("No open positions.")
    else:
        st.info("No open positions.")

# ---------------------------------------------------------------------------
# Section 6 — Trade History Table
# ---------------------------------------------------------------------------

with st.expander("📅 Today's Trades", expanded=False):
    if today_trades:
        try:
            import pandas as pd

            trade_rows = []
            for t in today_trades:
                trade_rows.append(
                    {
                        "Asset": t.get("asset", ""),
                        "Direction": t.get("direction", ""),
                        "Entry": f"{t.get('entry_price', 0):.4f}",
                        "Exit": f"{t.get('exit_price', 0):.4f}",
                        "P&L %": _fmt_pct(t.get("pnl_pct", 0)),
                        "Hold Bars": t.get("hold_bars", 0),
                        "Exit Reason": t.get("exit_reason", ""),
                        "Regime": t.get("regime_at_entry", ""),
                        "Strategy": t.get("strategy_name", ""),
                    }
                )
            df_trades = pd.DataFrame(trade_rows)
            st.dataframe(df_trades, use_container_width=True, hide_index=True)
        except Exception as exc:
            st.warning(f"Could not load trade history: {exc}")
    else:
        st.info("No trades recorded today.")

# ---------------------------------------------------------------------------
# Footer — auto-refresh info
# ---------------------------------------------------------------------------

st.markdown("---")
refresh_msg = (
    f"Auto-refresh every {refresh_seconds}s  ·  "
    f"Shared state: `{shared_state_path}`  ·  "
    f"Age: {age_seconds:.0f}s"
    if state
    else f"Waiting for shared_state.json at `{shared_state_path}`"
)
st.caption(refresh_msg)

# ---------------------------------------------------------------------------
# Fallback manual refresh (if streamlit-autorefresh not installed)
# ---------------------------------------------------------------------------

try:
    import streamlit_autorefresh  # noqa: F401 — already called above
except ImportError:
    col_ref, _ = st.columns([1, 5])
    with col_ref:
        if st.button("🔄 Refresh", key="manual_refresh"):
            st.rerun()
