#!/usr/bin/env bash
# Comprehensive health check for the ML Trader stack.
# Run from the project root on the Pi:
#   bash scripts/health_check.sh
#
# Reports PASS / WARN / FAIL for each subsystem. Exit code 0 means the
# system is ready for unattended operation. Exit code 1 means at least
# one critical check failed.

set -uo pipefail
cd "$(dirname "$0")/.."

# ── Output helpers ─────────────────────────────────────────────────────────
COL_PASS='\033[32m' ; COL_WARN='\033[33m' ; COL_FAIL='\033[31m'
COL_BOLD='\033[1m'  ; COL_DIM='\033[2m'   ; COL_RESET='\033[0m'

PASS_COUNT=0 ; WARN_COUNT=0 ; FAIL_COUNT=0

pass() { echo -e "  ${COL_PASS}✓ PASS${COL_RESET}  $1${2:+ ${COL_DIM}— $2${COL_RESET}}"; PASS_COUNT=$((PASS_COUNT+1)); }
warn() { echo -e "  ${COL_WARN}⚠ WARN${COL_RESET}  $1${2:+ ${COL_DIM}— $2${COL_RESET}}"; WARN_COUNT=$((WARN_COUNT+1)); }
fail() { echo -e "  ${COL_FAIL}✗ FAIL${COL_RESET}  $1${2:+ ${COL_DIM}— $2${COL_RESET}}"; FAIL_COUNT=$((FAIL_COUNT+1)); }
section() { echo -e "\n${COL_BOLD}── $1 ${COL_RESET}"; }

# ── 1. CONTAINERS ──────────────────────────────────────────────────────────
section "1. Containers"

if ! command -v docker >/dev/null 2>&1; then
  fail "docker not found"; exit 1
fi

trader_state=$(docker inspect mltrader-engine --format '{{.State.Status}}' 2>/dev/null || echo "missing")
dash_state=$(docker inspect mltrader-dashboard --format '{{.State.Status}}' 2>/dev/null || echo "missing")
trader_health=$(docker inspect mltrader-engine --format '{{.State.Health.Status}}' 2>/dev/null || echo "—")
trader_restart=$(docker inspect mltrader-engine --format '{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null || echo "—")
dash_restart=$(docker inspect mltrader-dashboard --format '{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null || echo "—")
trader_uptime=$(docker inspect mltrader-engine --format '{{.State.StartedAt}}' 2>/dev/null || echo "")
trader_restarts=$(docker inspect mltrader-engine --format '{{.RestartCount}}' 2>/dev/null || echo "?")

[[ "$trader_state" == "running" ]] && pass "trader running" "$trader_health" || fail "trader $trader_state"
[[ "$dash_state"   == "running" ]] && pass "dashboard running" || fail "dashboard $dash_state"
[[ "$trader_restart" == "unless-stopped" ]] && pass "trader restart policy = unless-stopped" || warn "trader restart policy = $trader_restart" "won't auto-restart on Pi reboot"
[[ "$dash_restart"   == "unless-stopped" ]] && pass "dashboard restart policy = unless-stopped" || warn "dashboard restart policy = $dash_restart"
if [[ "$trader_restarts" -gt 5 ]] 2>/dev/null; then
  warn "trader restart count = $trader_restarts" "may indicate instability"
else
  pass "trader restart count = $trader_restarts"
fi
[[ -n "$trader_uptime" ]] && echo -e "  ${COL_DIM}↳ trader started: $trader_uptime${COL_RESET}"

# ── 2. PI HARDWARE ─────────────────────────────────────────────────────────
section "2. Pi hardware"

if command -v vcgencmd >/dev/null 2>&1; then
  temp_raw=$(vcgencmd measure_temp 2>/dev/null | grep -oP '\d+\.\d+' || echo "0")
  temp_int=${temp_raw%.*}
  if   [[ "$temp_int" -lt 65 ]] 2>/dev/null; then pass "CPU temp ${temp_raw}°C" "comfortable"
  elif [[ "$temp_int" -lt 75 ]] 2>/dev/null; then warn "CPU temp ${temp_raw}°C" "acceptable but warm"
  else fail "CPU temp ${temp_raw}°C" "throttling risk — improve cooling"
  fi
else
  warn "vcgencmd not available" "skipping temperature check"
fi

mem_avail_mb=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
mem_total_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
mem_pct=$((100 * (mem_total_mb - mem_avail_mb) / mem_total_mb))
if   [[ "$mem_pct" -lt 70 ]]; then pass "memory ${mem_pct}% used" "${mem_avail_mb} MB free of ${mem_total_mb} MB"
elif [[ "$mem_pct" -lt 90 ]]; then warn "memory ${mem_pct}% used"
else fail "memory ${mem_pct}% used" "near OOM"
fi

disk_used_pct=$(df -P . | awk 'NR==2 {print int($5)}')
disk_avail_gb=$(df -BG . | awk 'NR==2 {sub("G","",$4); print $4}')
if   [[ "$disk_used_pct" -lt 70 ]]; then pass "disk ${disk_used_pct}% used" "${disk_avail_gb} GB free"
elif [[ "$disk_used_pct" -lt 85 ]]; then warn "disk ${disk_used_pct}% used" "${disk_avail_gb} GB free"
else fail "disk ${disk_used_pct}% used" "low headroom for 8-week run"
fi

# Project rate: logs+models size
project_mb=$(du -sm logs/ models/ data/ 2>/dev/null | tail -1 | awk '{ s+=$1 } END { print s+0 }')
echo -e "  ${COL_DIM}↳ logs+models+data total: ${project_mb} MB${COL_RESET}"

# ── 3. CONFIGURATION ──────────────────────────────────────────────────────
section "3. Configuration"

if [[ ! -f .env ]]; then fail ".env missing"; else
  pass ".env present"
  for k in ALPACA_API_KEY ALPACA_SECRET_KEY ALPACA_BASE_URL ASSETS HOST_PROJECT_DIR; do
    if grep -qE "^${k}=" .env; then
      val=$(grep -E "^${k}=" .env | head -1 | cut -d= -f2-)
      if [[ -z "$val" ]]; then fail "  $k is empty"
      elif [[ "$k" == ALPACA_API_KEY || "$k" == ALPACA_SECRET_KEY ]]; then
        pass "  $k set" "${#val} chars"
      else
        pass "  $k = $val"
      fi
    else
      fail "  $k missing from .env"
    fi
  done

  base_url=$(grep -E "^ALPACA_BASE_URL=" .env | head -1 | cut -d= -f2-)
  if [[ "$base_url" == *paper* ]]; then
    pass "trading mode = PAPER" "safety confirmed"
  else
    fail "trading mode = LIVE" "REFUSING to bless 8-week unattended LIVE — flip to paper before running this check"
  fi

  host_dir=$(grep -E "^HOST_PROJECT_DIR=" .env | head -1 | cut -d= -f2-)
  if [[ -d "$host_dir/logs" ]]; then
    pass "HOST_PROJECT_DIR is valid" "$host_dir"
  else
    warn "HOST_PROJECT_DIR=$host_dir doesn't contain logs/" "Live Trading Switch will fail"
  fi
fi

if [[ -f config/settings.yaml ]]; then
  if python3 -c "import yaml; yaml.safe_load(open('config/settings.yaml'))" 2>/dev/null; then
    pass "settings.yaml parses cleanly"
  else
    fail "settings.yaml YAML parse error"
  fi
else
  fail "config/settings.yaml missing"
fi

# ── 4. PERSISTENCE ─────────────────────────────────────────────────────────
section "4. Persistence files"

check_file() {
  local path=$1 label=$2 max_age_s=${3:-0}
  if [[ ! -f "$path" ]]; then fail "$label missing" "$path"; return; fi
  local size=$(stat -c %s "$path" 2>/dev/null || echo 0)
  local mtime=$(stat -c %Y "$path" 2>/dev/null || echo 0)
  local now=$(date +%s)
  local age=$((now - mtime))
  if [[ "$max_age_s" -gt 0 && "$age" -gt "$max_age_s" ]]; then
    warn "$label stale" "${age}s old (limit ${max_age_s}s) · ${size} bytes"
  else
    pass "$label" "${size} bytes · ${age}s old"
  fi
}

check_file logs/shared_state.json     "shared_state.json"   90
check_file logs/bar_archives.pkl      "bar_archives.pkl"
check_file logs/feature_history.pkl   "feature_history.pkl"

# Models
ls models/*_hmm.pkl >/dev/null 2>&1 \
  && pass "HMM model(s) present" "$(ls models/*_hmm.pkl | xargs -n1 basename | tr '\n' ' ')" \
  || warn "no HMM model in models/" "first retrain hasn't completed"

# Log files writability
for log in logs/app.log logs/trades.log logs/orders.log logs/regime.log logs/session.log logs/pnl.log; do
  if [[ -f "$log" ]]; then
    [[ -w "$log" ]] && pass "$(basename $log) writable" "$(stat -c %s $log) bytes" || fail "$(basename $log) not writable"
  fi
done

# ── 5. ALPACA — auth + account fetch + paper safety ───────────────────────
section "5. Alpaca external service"

if docker exec mltrader-engine python -c "
import os, sys
from alpaca.trading.client import TradingClient
key = os.environ.get('ALPACA_API_KEY'); sec = os.environ.get('ALPACA_SECRET_KEY')
url = os.environ.get('ALPACA_BASE_URL','')
if not key or not sec:
    print('ENV_MISSING'); sys.exit(2)
if 'paper' not in url:
    print('NOT_PAPER'); sys.exit(3)
c = TradingClient(api_key=key, secret_key=sec, paper=True)
a = c.get_account()
print(f'OK|{a.status}|{a.equity}|{a.cash}|{a.buying_power}|{a.pattern_day_trader}|{a.trading_blocked}|{a.account_blocked}')
" 2>/tmp/alpaca_err; then
  result=$(docker exec mltrader-engine python -c "
import os
from alpaca.trading.client import TradingClient
key = os.environ.get('ALPACA_API_KEY'); sec = os.environ.get('ALPACA_SECRET_KEY')
c = TradingClient(api_key=key, secret_key=sec, paper=True)
a = c.get_account()
print(f'{a.status}|{a.equity}|{a.cash}|{a.buying_power}|{a.pattern_day_trader}|{a.trading_blocked}|{a.account_blocked}')
" 2>/dev/null)
  IFS='|' read -r status equity cash bp pdt trade_blk acct_blk <<< "$result"
  pass "Alpaca auth + account fetch" "status=$status equity=\$$equity"
  [[ "$trade_blk" == "False" ]] && pass "trading not blocked" || fail "trading_blocked=$trade_blk"
  [[ "$acct_blk"  == "False" ]] && pass "account not blocked" || fail "account_blocked=$acct_blk"
  [[ "$pdt"       == "False" ]] && pass "PDT flag clear" || warn "PDT flag set" "may restrict day-trades"
else
  err=$(cat /tmp/alpaca_err)
  fail "Alpaca account fetch failed" "$(echo "$err" | tail -1)"
fi

# Network reachability separately
if docker exec mltrader-engine python -c "import socket; socket.create_connection(('paper-api.alpaca.markets', 443), timeout=5)" 2>/dev/null; then
  pass "paper-api.alpaca.markets:443 reachable"
else
  fail "cannot reach paper-api.alpaca.markets" "check Pi internet"
fi

if docker exec mltrader-engine python -c "import socket; socket.create_connection(('stream.data.alpaca.markets', 443), timeout=5)" 2>/dev/null; then
  pass "data.alpaca.markets:443 reachable" "websocket bar stream endpoint"
else
  fail "cannot reach stream.data.alpaca.markets"
fi

# ── 6. ENGINE STATE & BEHAVIOR ─────────────────────────────────────────────
section "6. Engine state"

if [[ -f logs/shared_state.json ]]; then
  python3 << 'PYEOF'
import json, os, time
from datetime import datetime, timezone
s = json.load(open('logs/shared_state.json'))
mtime = os.path.getmtime('logs/shared_state.json')
age = time.time() - mtime
items = []
items.append(("hmm_trained",        s.get("hmm_trained"), s.get("hmm_trained") is True))
items.append(("training_bars",      f"{s.get('training_bars',0)}/{s.get('training_needed',390)}", s.get("training_bars",0) >= s.get("training_needed",390)))
fw = s.get("feature_warmup", {})
all_ready = all(v.get("ready") for v in fw.values()) if fw else False
items.append(("feature_warmup",     "ready" if all_ready else f"warming ({sum(v['bars'] for v in fw.values())}/{sum(v['needed'] for v in fw.values())})", all_ready))
ri = s.get("regime_info", {})
known = any(v.get("regime","UNKNOWN") != "UNKNOWN" for v in ri.values())
items.append(("regime_known",       ", ".join(f"{k}={v.get('regime','?')}({v.get('confidence',0):.2f})" for k,v in ri.items()), known))
items.append(("circuit_breaker",    s.get("circuit_breaker_active"), s.get("circuit_breaker_active") is False))
for label, value, ok in items:
    sym = "\033[32m✓ PASS\033[0m" if ok else "\033[33m⚠ WARN\033[0m"
    print(f"  {sym}  {label:18s} {value}")
PYEOF
fi

# Recent retrain success?
recent_retrain=$(docker compose logs trader --since 24h 2>&1 | grep -E "RETRAIN_COMPLETE|RETRAIN_FAILED" | tail -3)
if echo "$recent_retrain" | grep -q "RETRAIN_COMPLETE"; then
  pass "recent RETRAIN_COMPLETE in last 24h"
elif echo "$recent_retrain" | grep -q "RETRAIN_FAILED"; then
  fail "RETRAIN_FAILED in last 24h" "$(echo $recent_retrain | tail -1 | head -c 100)"
else
  warn "no retrain events in last 24h" "expected if HMM was trained earlier and bars haven't crossed retrain_every_bars"
fi

# Recent activity
last_signal=$(python3 -c "import json; s=json.load(open('logs/shared_state.json')); sig=s.get('last_10_signals',[]); print(sig[-1]['ts'] if sig else 'none')" 2>/dev/null || echo "?")
echo -e "  ${COL_DIM}↳ last signal timestamp: $last_signal${COL_RESET}"

# ── 7. DASHBOARD ───────────────────────────────────────────────────────────
section "7. Dashboard"

if curl -fsS -o /dev/null -w "%{http_code}" http://localhost:8501/ 2>/dev/null | grep -q 200; then
  pass "dashboard root HTTP 200"
else
  fail "dashboard root not responding"
fi

api_ok=true
for ep in /api/state /api/meta /api/trades /api/regime-history /api/config; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8501${ep}" 2>/dev/null)
  if [[ "$code" == "200" ]]; then
    pass "API $ep" "200"
  else
    fail "API $ep" "$code"
    api_ok=false
  fi
done

# Verify Live Trading Switch is locked (mode=PAPER) — defensive check
mode=$(curl -s http://localhost:8501/api/meta 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('mode'))" 2>/dev/null || echo "?")
[[ "$mode" == "PAPER" ]] && pass "/api/meta reports mode=PAPER" || fail "/api/meta reports mode=$mode" "Live Trading Switch unlocked!"

# ── 8. AUTONOMY / SURVIVABILITY ────────────────────────────────────────────
section "8. Autonomy & survivability"

# Log rotation configured?
log_max=$(docker inspect mltrader-engine --format '{{.HostConfig.LogConfig.Config.max-size}}' 2>/dev/null)
log_files=$(docker inspect mltrader-engine --format '{{.HostConfig.LogConfig.Config.max-file}}' 2>/dev/null)
if [[ -n "$log_max" && "$log_max" != "<no value>" ]]; then
  pass "trader log rotation" "max ${log_max} × ${log_files} files"
else
  warn "trader log rotation not configured" "logs may grow unbounded"
fi

# Pi reboot survival
if [[ "$(systemctl is-enabled docker 2>/dev/null)" == "enabled" ]]; then
  pass "docker.service enabled at boot" "containers will come back after Pi reboot"
else
  warn "docker.service not enabled at boot" "containers won't restart automatically after Pi reboot"
fi

# Disk projection: assume current logs+models size is N days old
if [[ "$project_mb" -gt 0 ]]; then
  bot_age_days=$(( ($(date +%s) - $(stat -c %Y logs 2>/dev/null || echo $(date +%s))) / 86400 ))
  if [[ "$bot_age_days" -gt 0 ]]; then
    daily_mb=$((project_mb / bot_age_days))
    proj_8w=$((daily_mb * 56))
    echo -e "  ${COL_DIM}↳ growth rate: ~${daily_mb} MB/day · projected 8-week total: ~${proj_8w} MB${COL_RESET}"
    if [[ "$proj_8w" -lt 5000 ]]; then
      pass "8-week disk projection" "~${proj_8w} MB"
    else
      warn "8-week disk projection" "~${proj_8w} MB — verify free space"
    fi
  fi
fi

# DST check
month=$(date +%m); day=$(date +%d)
echo -e "  ${COL_DIM}↳ Today is $(date +%Y-%m-%d). US DST changes Nov 1 (UTC-5) and Mar 8 (UTC-4) — check session windows still align after each shift.${COL_RESET}"

# ── SUMMARY ────────────────────────────────────────────────────────────────
section "Summary"
total=$((PASS_COUNT + WARN_COUNT + FAIL_COUNT))
echo -e "  ${COL_PASS}${PASS_COUNT} PASS${COL_RESET}  ${COL_WARN}${WARN_COUNT} WARN${COL_RESET}  ${COL_FAIL}${FAIL_COUNT} FAIL${COL_RESET}  (${total} total)"
echo

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if [[ "$WARN_COUNT" -eq 0 ]]; then
    echo -e "${COL_PASS}${COL_BOLD}  ✓ READY FOR 8-WEEK UNATTENDED RUN${COL_RESET}"
  else
    echo -e "${COL_WARN}${COL_BOLD}  ⚠ READY WITH WARNINGS — review WARN items above${COL_RESET}"
  fi
  exit 0
else
  echo -e "${COL_FAIL}${COL_BOLD}  ✗ NOT READY — resolve FAIL items before unattended operation${COL_RESET}"
  exit 1
fi
