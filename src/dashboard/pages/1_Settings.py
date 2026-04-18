"""⚙️ Settings — Broker credentials & trading mode.

Accessed via the sidebar navigation when running:
    streamlit run src/dashboard/app.py

Security notes:
- Credentials are written to the project .env file (plaintext).
  This is acceptable for a localhost-only setup; never expose this
  dashboard on a network interface.
- Keys are never echoed back to the UI — only the last 4 characters
  are shown as a masked placeholder.
- Changes take effect only after a full engine restart (main.py
  reads the .env once at startup via python-dotenv).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import streamlit as st

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="⚙️ Settings — ML Trader",
    page_icon="⚙️",
    layout="centered",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"

# Project root is 3 levels up from this file (pages/ → dashboard/ → src/ → root)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ENV_PATH = _PROJECT_ROOT / ".env"

# ---------------------------------------------------------------------------
# Custom CSS — match main dashboard palette
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
        /* ═══════════════════════════════════════════════
           BASE
        ═══════════════════════════════════════════════ */
        .block-container { padding-top: 2rem; max-width: 720px; }

        /* ── Section divider cards ── */
        .settings-section {
            background: linear-gradient(160deg, #1a2040 0%, #161b2e 100%);
            border: 1px solid #1f2235;
            border-radius: 12px;
            padding: 20px 22px;
            margin-bottom: 1.2rem;
        }

        /* ── Status badges ── */
        .paper-badge {
            display: inline-flex; align-items: center; gap: 6px;
            background: #1c3040; border: 1px solid #546E7A;
            color: #80CBC4; border-radius: 20px;
            padding: 4px 14px; font-weight: 700; font-size: 0.85rem;
            letter-spacing: 0.03em;
        }
        .live-badge {
            display: inline-flex; align-items: center; gap: 6px;
            background: #3e1111; border: 1px solid #EF5350;
            color: #EF9A9A; border-radius: 20px;
            padding: 4px 14px; font-weight: 700; font-size: 0.85rem;
            letter-spacing: 0.03em;
        }

        /* ── Live mode warning banner ── */
        .live-warning {
            background: linear-gradient(135deg, #b71c1c 0%, #c62828 100%);
            color: #fff; border-radius: 10px;
            padding: 14px 18px; font-weight: 700; font-size: 1rem;
            margin-bottom: 1rem;
            border-left: 4px solid #ff5252;
            box-shadow: 0 4px 16px rgba(239,83,80,0.30);
        }

        /* ── Restart banner ── */
        .restart-banner {
            background: linear-gradient(135deg, #0d1b4b 0%, #1a237e 100%);
            color: #c5cae9; border-radius: 8px;
            padding: 12px 16px; font-size: 0.9rem; margin-top: 0.5rem;
            border-left: 3px solid #2196F3;
        }

        /* ── Primary button (gradient blue) ── */
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
            transform: translateY(0) !important;
        }
        [data-testid="baseButton-primary"]:disabled {
            opacity: 0.35 !important; cursor: not-allowed !important;
        }

        /* ── Secondary button ── */
        [data-testid="baseButton-secondary"] {
            border: 1px solid #2a3050 !important;
            border-radius: 8px !important;
            background: transparent !important;
            color: #9aa0b4 !important;
            transition: border-color 0.15s, color 0.15s, background 0.15s !important;
        }
        [data-testid="baseButton-secondary"]:hover {
            border-color: #2196F3 !important;
            color: #e0e6ff !important;
            background: rgba(33,150,243,0.07) !important;
        }

        /* ═══════════════════════════════════════════════
           MOBILE  ≤ 768 px
        ═══════════════════════════════════════════════ */
        @media screen and (max-width: 768px) {
            .block-container {
                padding-left: 0.6rem !important;
                padding-right: 0.6rem !important;
                max-width: 100% !important;
            }
            [data-testid="stHorizontalBlock"] {
                flex-direction: column !important; gap: 0 !important;
            }
            [data-testid="column"] {
                width: 100% !important; flex: 1 1 100% !important; min-width: 0 !important;
            }
            h1 { font-size: 1.3rem !important; }
            h2, h3 { font-size: 1rem !important; }
            [data-testid="baseButton-primary"],
            [data-testid="baseButton-secondary"] {
                min-height: 48px !important; font-size: 1rem !important;
            }
            .live-warning { font-size: 0.88rem !important; padding: 10px 12px !important; }
            .settings-section { padding: 14px 12px !important; }
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_env() -> dict[str, str]:
    """Parse .env file into a key→value dict. Missing file returns {}."""
    result: dict[str, str] = {}
    if not _ENV_PATH.exists():
        return result
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _write_env(values: dict[str, str]) -> None:
    """Write key→value pairs to .env, preserving comments and unrelated keys."""
    existing_lines: list[str] = []
    if _ENV_PATH.exists():
        existing_lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()

    written_keys: set[str] = set()
    new_lines: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            new_lines.append(line)
            continue
        if "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in values:
                new_lines.append(f"{k}={values[k]}")
                written_keys.add(k)
                continue
        new_lines.append(line)

    # Append any new keys not previously in the file
    for k, v in values.items():
        if k not in written_keys:
            new_lines.append(f"{k}={v}")

    _ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _mask(value: str, show_last: int = 4) -> str:
    """Return a masked version showing only the last N characters."""
    if not value:
        return ""
    if len(value) <= show_last:
        return "*" * len(value)
    return "•" * (len(value) - show_last) + value[-show_last:]


def _detect_mode(base_url: str) -> str:
    """Return 'paper' or 'live' based on the stored URL."""
    if "paper" in base_url.lower():
        return "paper"
    if base_url.strip():
        return "live"
    return "paper"  # default to paper when unset


# ---------------------------------------------------------------------------
# Load current .env on first render
# ---------------------------------------------------------------------------

if "settings_env" not in st.session_state:
    st.session_state["settings_env"] = _read_env()

env = st.session_state["settings_env"]

current_api_key: str = env.get("ALPACA_API_KEY", "")
current_secret_key: str = env.get("ALPACA_SECRET_KEY", "")
current_base_url: str = env.get("ALPACA_BASE_URL", PAPER_URL)
current_mode: str = _detect_mode(current_base_url)

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.title("⚙️ Broker & API Settings")
st.caption(
    "Changes are written to `.env` and take effect after the engine restarts. "
    "The live engine is not interrupted until you restart it."
)

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 1 — Trading Mode
# ---------------------------------------------------------------------------

st.subheader("🔄 Trading Mode")

mode_choice = st.radio(
    label="Select mode",
    options=["📋  Paper Trading", "🔴  Live Trading"],
    index=0 if current_mode == "paper" else 1,
    horizontal=True,
    help=(
        "Paper: uses Alpaca paper-api endpoint — no real money.\n"
        "Live: uses Alpaca live endpoint — REAL MONEY at risk."
    ),
)

selected_mode = "live" if "Live" in mode_choice else "paper"
selected_url = LIVE_URL if selected_mode == "live" else PAPER_URL

# Show appropriate badge
if selected_mode == "live":
    st.markdown(
        '<div class="live-warning">'
        "🚨 LIVE TRADING SELECTED — Orders will use REAL MONEY. "
        "Double-check your API key belongs to a live Alpaca account before saving."
        "</div>",
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<span class="paper-badge">📋 PAPER MODE — No real money at risk</span>',
        unsafe_allow_html=True,
    )

st.markdown("")  # spacing

# Endpoint display (read-only — auto-set by mode selection)
st.text_input(
    "Broker Endpoint (auto-set by mode)",
    value=selected_url,
    disabled=True,
    help="Set automatically based on the mode selected above.",
)

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 2 — API Credentials
# ---------------------------------------------------------------------------

st.subheader("🔑 API Credentials")

st.caption(
    "Leave a field blank to keep the existing value. "
    f"Current keys: API `{_mask(current_api_key)}` · Secret `{_mask(current_secret_key)}`"
)

col_key, col_secret = st.columns(2, gap="medium")

with col_key:
    new_api_key = st.text_input(
        "Alpaca API Key",
        value="",
        type="password",
        placeholder="Paste new key, or leave blank to keep current",
        help="Found in your Alpaca dashboard under API Keys.",
    )

with col_secret:
    new_secret_key = st.text_input(
        "Alpaca Secret Key",
        value="",
        type="password",
        placeholder="Paste new secret, or leave blank to keep current",
        help="Only shown once in Alpaca — regenerate if lost.",
    )

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 3 — Confirmation gate (live mode only)
# ---------------------------------------------------------------------------

save_blocked = False

if selected_mode == "live":
    confirmed = st.checkbox(
        "✅  I confirm this API key belongs to a **LIVE** Alpaca account "
        "and I accept that real money will be at risk.",
        value=False,
        key="live_confirm",
    )
    if not confirmed:
        st.warning(
            "You must confirm the checkbox above before saving Live mode.",
            icon="⚠️",
        )
        save_blocked = True
else:
    # Reset confirmation when switching back to paper
    if "live_confirm" in st.session_state:
        st.session_state["live_confirm"] = False

# ---------------------------------------------------------------------------
# Section 4 — Save button
# ---------------------------------------------------------------------------

save_label = "💾  Save Settings" if not save_blocked else "🔒  Confirm live mode above to save"

if st.button(
    save_label,
    type="primary",
    use_container_width=True,
    disabled=save_blocked,
):
    updates: dict[str, str] = {"ALPACA_BASE_URL": selected_url}

    # Only overwrite keys if the user actually typed something
    if new_api_key.strip():
        updates["ALPACA_API_KEY"] = new_api_key.strip()
    if new_secret_key.strip():
        updates["ALPACA_SECRET_KEY"] = new_secret_key.strip()

    try:
        _write_env(updates)
        # Refresh cached env so masked values update
        st.session_state["settings_env"] = _read_env()
        st.success(
            "✅ Settings saved to `.env`.",
            icon=None,
        )
        st.markdown(
            '<div class="restart-banner">'
            "🔄 <strong>Restart required.</strong> Stop and restart "
            "<code>python main.py</code> for changes to take effect. "
            "The running engine is still using the previous settings."
            "</div>",
            unsafe_allow_html=True,
        )
    except Exception as exc:
        st.error(f"Failed to write `.env`: {exc}")

# ---------------------------------------------------------------------------
# Section 5 — Current active state (read from OS env, not .env file)
# ---------------------------------------------------------------------------

st.markdown("---")
st.subheader("⚡ Active Engine State")
st.caption(
    "This reflects what the **running engine** is actually using "
    "(loaded from OS environment at startup). It may differ from the saved "
    "`.env` if you have unsaved changes or haven't restarted yet."
)

active_url = os.environ.get("ALPACA_BASE_URL", "not set")
active_api = os.environ.get("ALPACA_API_KEY", "")
active_mode = _detect_mode(active_url)

col_a, col_b = st.columns(2)
with col_a:
    if active_mode == "live":
        st.markdown(
            '<span class="live-badge">🔴 LIVE — Real Money</span>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<span class="paper-badge">📋 PAPER — Simulated</span>',
            unsafe_allow_html=True,
        )
    st.caption(f"Endpoint: `{active_url}`")

with col_b:
    st.caption(f"API Key (active): `{_mask(active_api)}`")
    st.caption("Secret Key: `••••••••••••` (never shown)")
