---
# ============================================================
# ML TRADER DIABLO v1 — DAILY TROUBLESHOOTING & TELEMETRY LOG
# ============================================================
schema_version: "1.0"
date: "YYYY-MM-DD"
day_label: "DD-Weekday-Month-YYYY"          # e.g. 21-Tuesday-April-2026
environment: "paper"                         # paper | live
host: "raspberrypi"
containers:
  trader:    "mltrader-engine"
  dashboard: "mltrader-dashboard"
log_file: "logs/troubleshooting/DD-Weekday-Month-YYYY-troubleshooting.md"
generated_by: "mltrader-engine"
market_open_utc:  "13:30:00Z"
market_close_utc: "20:00:00Z"
timezone_primary: "America/New_York"
timezone_operator: "Africa/Johannesburg"
open_issues_count: 0
resolved_issues_count: 0
---

<!--
╔══════════════════════════════════════════════════════════════════════════════╗
║  AI INGESTION INSTRUCTIONS — READ THIS BEFORE ANALYSING THE DOCUMENT        ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  This file is the single source of truth for one calendar day of runtime    ║
║  telemetry for ML Trader Diablo v1 (HMM + LightGBM, Alpaca broker,          ║
║  Raspberry Pi 5 deployment).  It is designed for both human skimming and     ║
║  structured AI ingestion.                                                    ║
║                                                                              ║
║  PARSING CONTRACT                                                            ║
║  • YAML frontmatter (between the --- delimiters) contains machine-readable  ║
║    metadata about the day.  Parse it first for context.                      ║
║  • Each log entry is a Markdown level-4 heading (####) whose text encodes   ║
║    timestamp + severity + component in a fixed format:                       ║
║      #### [HH:MM:SS.mmmZ] SEVERITY — COMPONENT                              ║
║  • Every entry contains a ```json state block and, when applicable, a       ║
║    ```python (or ```bash) traceback block.  Extract these programmatically.  ║
║  • Sections are level-2 headings (##).  All entries under a section belong  ║
║    to that category.  Never mix categories.                                  ║
║  • The Daily Performance Summary section (last) is always written exactly   ║
║    once, at EOD.  Use it for trend analysis across multiple day files.       ║
║                                                                              ║
║  SEVERITY ENUM (strict — never deviate)                                     ║
║    🔴 CRITICAL  System-halting event. Immediate human action required.       ║
║    🟠 HIGH      Significant degradation. Action required within 1 hour.      ║
║    🟡 MEDIUM    Degraded behaviour. Investigate same day.                    ║
║    🔵 LOW       Minor anomaly. Log and monitor.                              ║
║    ⚪ INFO      Nominal operational event. No action required.               ║
║                                                                              ║
║  COMPONENT IDENTIFIERS                                                       ║
║    trader-engine      Main asyncio loop (main.py)                           ║
║    alpaca-stream      WebSocket streaming thread                             ║
║    hmm-engine         Hidden Markov Model (src/brain/hmm_engine.py)         ║
║    lgbm-router        LightGBM expert router (src/brain/lgbm_experts.py)    ║
║    feature-engineer   Feature computation (src/brain/feature_engineering.py)║
║    risk-manager       Risk + circuit breaker (src/risk/)                    ║
║    broker-executor    Order submission (src/broker/)                        ║
║    session-manager    Session boundary logic (src/session/)                 ║
║    structured-logger  Log writer + shared_state.json (src/monitoring/)      ║
║    dashboard          Streamlit UI (src/dashboard/)                         ║
║    docker-host        Container / Pi OS infrastructure                      ║
║                                                                              ║
║  HOW TO USE THIS FILE FOR REMEDIATION                                        ║
║  1. Parse the frontmatter for day-level context.                             ║
║  2. Filter entries by severity (🔴 / 🟠 first).                              ║
║  3. For each critical entry, read the ```json state block to understand      ║
║     the exact runtime state at the moment of failure.                        ║
║  4. Read the traceback block to locate the failing line.                     ║
║  5. Cross-reference the Component Identifier with the source file listed     ║
║     above, then propose a targeted fix.                                      ║
║  6. Check the Daily Performance Summary for patterns (e.g. repeated          ║
║     failures at the same UTC minute → likely a scheduling bug).              ║
╚══════════════════════════════════════════════════════════════════════════════╝
-->

# 🗓️ DD-Weekday-Month-YYYY — Daily Troubleshooting Log

> **Environment:** `paper` &nbsp;|&nbsp; **Host:** `raspberrypi` &nbsp;|&nbsp; **Operator TZ:** SAST (UTC+2)
> **Market hours (ET):** 09:30 – 16:00 &nbsp;|&nbsp; **Market hours (UTC):** 13:30 – 20:00

---

## 📋 How to Append an Entry

The logging system **appends** entries beneath the correct `##` section heading.
Every entry must follow this exact schema — no exceptions:

```
#### [HH:MM:SS.mmmZ] 🟡 MEDIUM — component-name

**Event:** One-sentence plain-English description of what went wrong.

**State Context:**
\```json
{
  "key": "value",
  "relevant_variable": 123,
  "asset": "SPY",
  "timestamp": "ISO-8601"
}
\```

**Stacktrace:** *(omit block entirely if not applicable)*
\```python
Traceback (most recent call last):
  File "src/module.py", line NN, in function_name
    offending_line_of_code
ExceptionType: human-readable message
\```

**Resolution:** *(fill in when fixed — leave blank if open)*
```

---

## 🖥️ 1 — System Health & Infrastructure

> CPU, memory, disk usage, container states, Pi hardware events.

---

#### [09:31:02.441Z] ⚪ INFO — docker-host

**Event:** Both containers started successfully and passed health checks after scheduled overnight restart.

**State Context:**
```json
{
  "containers": {
    "mltrader-engine":    { "status": "running", "uptime_seconds": 62 },
    "mltrader-dashboard": { "status": "running", "uptime_seconds": 5 }
  },
  "host": {
    "cpu_temp_c": 42.1,
    "cpu_load_1m": 0.18,
    "mem_used_mb": 1124,
    "mem_total_mb": 8192,
    "disk_used_pct": 23.4
  },
  "timestamp": "YYYY-MM-DDTHH:MM:SS.sssZ"
}
```

**Resolution:** N/A — nominal startup.

---

#### [13:28:55.007Z] 🟡 MEDIUM — docker-host

**Event:** CPU temperature spiked to 78 °C during HMM retrain background task; thermal throttling engaged briefly.

**State Context:**
```json
{
  "cpu_temp_c": 78.2,
  "cpu_load_1m": 3.91,
  "throttled_flag": true,
  "active_task": "hmm-retrain-SPY",
  "cpuset_trader": "0,1",
  "cpuset_dashboard": "2,3",
  "timestamp": "YYYY-MM-DDTHH:MM:SS.sssZ"
}
```

**Resolution:** Throttling cleared within 45 s. Consider adding `--cpus 1.5` cap to trader service if recurring.

---

## ⚙️ 2 — Functionality & Application Logic

> Feature failures, unexpected model states, regime anomalies, signal errors.

---

#### [13:35:18.209Z] ⚪ INFO — hmm-engine

**Event:** HMM training threshold reached (400 bars); initial model fitted successfully for SPY.

**State Context:**
```json
{
  "asset": "SPY",
  "bars_accumulated": 400,
  "n_components_selected": 4,
  "covariance_type": "full",
  "fit_duration_ms": 1843,
  "initial_regime": "CHOPPY",
  "confidence": 0.61,
  "timestamp": "YYYY-MM-DDTHH:MM:SS.sssZ"
}
```

**Resolution:** N/A — nominal first training.

---

#### [15:02:44.882Z] 🟠 HIGH — feature-engineer

**Event:** `compute_features()` returned `None` for 3 consecutive bars due to NaN in realized-vol window; HMM inference skipped for those bars.

**State Context:**
```json
{
  "asset": "SPY",
  "bars_since_open": 93,
  "realized_vol_window": 20,
  "nan_columns": ["realized_vol", "vol_ratio"],
  "last_valid_bar_ts": "YYYY-MM-DDTHH:MM:SS.sssZ",
  "consecutive_null_bars": 3,
  "timestamp": "YYYY-MM-DDTHH:MM:SS.sssZ"
}
```

**Stacktrace:**
```python
Traceback (most recent call last):
  File "src/brain/feature_engineering.py", line 214, in compute_features
    features["realized_vol"] = self._vol_window.compute()
  File "src/brain/feature_engineering.py", line 88, in compute
    return float(np.std(self._buffer) * np.sqrt(252 * 26))
ValueError: cannot convert float NaN to integer
```

**Resolution:** *(open — investigate whether zero-volume bars during early session are corrupting the rolling buffer)*

---

## 🌐 3 — Network & Connection Stats

> WebSocket latency, Alpaca API response times, reconnect events, SMTP delivery.

---

#### [09:30:01.003Z] ⚪ INFO — alpaca-stream

**Event:** Stock WebSocket connected and subscribed to SPY minute bars.

**State Context:**
```json
{
  "stream": "StockDataStream",
  "symbols": ["SPY"],
  "bar_size": "1Min",
  "connect_latency_ms": 312,
  "auth_latency_ms": 88,
  "timestamp": "YYYY-MM-DDTHH:MM:SS.sssZ"
}
```

**Resolution:** N/A — nominal connection.

---

#### [14:17:33.561Z] 🟠 HIGH — alpaca-stream

**Event:** WebSocket connection dropped mid-session; exponential-backoff reconnect triggered (attempt 1/10).

**State Context:**
```json
{
  "stream": "stock",
  "reconnect_attempt": 1,
  "max_attempts": 10,
  "backoff_delay_s": 1.0,
  "last_bar_received_ts": "YYYY-MM-DDTHH:MM:SS.sssZ",
  "bars_missed_estimate": 2,
  "timestamp": "YYYY-MM-DDTHH:MM:SS.sssZ"
}
```

**Resolution:** Reconnected at 14:17:36Z (3 s downtime). 2 bars missed — HMM continuity unaffected.

---

#### [16:02:10.774Z] 🟡 MEDIUM — structured-logger

**Event:** EOD daily performance email delivery failed — SMTP authentication error.

**State Context:**
```json
{
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "smtp_user_set": false,
  "smtp_password_set": false,
  "recipient": "ddiablonuva@gmail.com",
  "report_type": "daily",
  "error": "SMTPAuthenticationError: 535 Username and Password not accepted",
  "timestamp": "YYYY-MM-DDTHH:MM:SS.sssZ"
}
```

**Resolution:** *(open — add SMTP_USER and SMTP_PASSWORD to .env on the Pi)*

---

## 💥 4 — Crash & Fatal Events

> Unhandled exceptions, OOM kills, container exits, circuit breaker activations.

---

#### [00:00:00.000Z] ⚪ INFO — trader-engine

**Event:** No crash or fatal events recorded for this trading day.

**State Context:**
```json
{
  "fatal_events": 0,
  "container_restarts": 0,
  "circuit_breaker_activations": 0,
  "timestamp": "YYYY-MM-DDTHH:MM:SS.sssZ"
}
```

**Resolution:** N/A.

---

<!-- EXAMPLE of how a real crash entry looks: -->
<!--
#### [15:44:02.019Z] 🔴 CRITICAL — trader-engine

**Event:** Unhandled exception in `on_bar()` caused main loop to exit; container restarted by Docker.

**State Context:**
```json
{
  "asset": "SPY",
  "bar_timestamp": "YYYY-MM-DDTHH:MM:SS.sssZ",
  "regime": "TRENDING_UP",
  "hmm_trained": true,
  "open_positions": 1,
  "equity": 99820.00,
  "exception_type": "KeyError",
  "exception_message": "'stop_price'",
  "timestamp": "YYYY-MM-DDTHH:MM:SS.sssZ"
}
```

**Stacktrace:**
```python
Traceback (most recent call last):
  File "main.py", line 384, in on_bar
    decision = self._risk_manager.evaluate(signal, self._portfolio_state, now)
  File "src/risk/risk_manager.py", line 119, in evaluate
    stop = portfolio_state.positions[asset]["stop_price"]
KeyError: 'stop_price'
```

**Resolution:** Fixed in commit abc1234 — PortfolioState.Position now initialises stop_price to None.
-->

---

## 📊 5 — Daily Performance Summary

> Written **once**, at end-of-day (≥ 15:55 ET / 19:55 UTC) by the performance reporter.
> Leave this section blank until EOD — the reporter appends it automatically.

---

#### [19:55:01.000Z] ⚪ INFO — structured-logger

**Event:** End-of-day performance summary generated.

**State Context:**
```json
{
  "date": "YYYY-MM-DD",
  "trading_day": true,
  "session_open_utc":  "13:30:00Z",
  "session_close_utc": "20:00:00Z",
  "equity_open":  100000.00,
  "equity_close": 100000.00,
  "daily_pnl_dollar": 0.00,
  "daily_pnl_pct": 0.0000,
  "total_trades": 0,
  "winning_trades": 0,
  "losing_trades": 0,
  "win_rate_pct": null,
  "avg_pnl_pct": null,
  "best_trade_pct": null,
  "worst_trade_pct": null,
  "regimes_observed": ["UNKNOWN"],
  "hmm_trained": false,
  "bars_accumulated": 390,
  "bars_needed_for_training": 400,
  "circuit_breaker_triggered": false,
  "open_issues": 2,
  "resolved_issues": 1,
  "email_report_sent": false,
  "timestamp": "YYYY-MM-DDTHH:MM:SS.sssZ"
}
```

**Resolution:** N/A — daily summary.

---

*End of log — DD-Weekday-Month-YYYY*
