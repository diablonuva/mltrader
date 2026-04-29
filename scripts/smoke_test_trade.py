#!/usr/bin/env python3
"""Smoke-test trade — places one paper order via Alpaca, holds, then closes.

Usage (run from project root, inside the trader container or any env with
the Alpaca SDK installed and ALPACA_API_KEY/SECRET in the environment):

    docker compose exec trader python scripts/smoke_test_trade.py
    docker compose exec trader python scripts/smoke_test_trade.py --hold 10 --shares 1
    docker compose exec trader python scripts/smoke_test_trade.py --symbol QQQ --hold 30

Confirms end-to-end Alpaca paper integration:
  - Order placement
  - Fill confirmation
  - Cash/equity debit & credit
  - Realized P&L computation
  - Order history records

Does NOT touch the bot's signal pipeline. The bot will still see the position
appear (via its periodic Alpaca poll) and surface it on the dashboard.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
except ImportError:
    sys.exit("alpaca-py not installed in this environment")


# ── Helpers ──────────────────────────────────────────────────────────────────

def ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

def hr(char: str = "─", n: int = 64) -> None:
    print(char * n)

def show_account(client: TradingClient, label: str) -> dict:
    a = client.get_account()
    snap = {
        "equity":        float(a.equity),
        "cash":          float(a.cash),
        "buying_power":  float(a.buying_power),
        "portfolio_val": float(a.portfolio_value),
        "long_market":   float(a.long_market_value or 0),
    }
    print(f"\n[{label}]   {ts()}")
    print(f"  equity:           ${snap['equity']:>14,.2f}")
    print(f"  cash:             ${snap['cash']:>14,.2f}")
    print(f"  buying power:     ${snap['buying_power']:>14,.2f}")
    print(f"  portfolio value:  ${snap['portfolio_val']:>14,.2f}")
    print(f"  long mkt value:   ${snap['long_market']:>14,.2f}")
    return snap

def show_position(client: TradingClient, symbol: str) -> None:
    try:
        pos = client.get_open_position(symbol)
        print(f"  position [{symbol}]: {pos.qty} sh @ avg ${float(pos.avg_entry_price):.2f}  "
              f"current ${float(pos.current_price):.2f}  "
              f"unrealised P&L: ${float(pos.unrealized_pl):.2f}")
    except Exception:
        print(f"  position [{symbol}]: none")

def submit_market(client: TradingClient, symbol: str, qty: int, side: OrderSide) -> str:
    req = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=side,
        time_in_force=TimeInForce.DAY,
    )
    order = client.submit_order(req)
    print(f"\n[ORDER SUBMITTED]  {ts()}")
    print(f"  id:        {order.id}")
    print(f"  side:      {order.side.value}")
    print(f"  symbol:    {order.symbol}  qty={order.qty}")
    print(f"  status:    {order.status.value}")
    return str(order.id)

def wait_for_fill(client: TradingClient, order_id: str, timeout_s: int = 30) -> bool:
    print(f"\n[WAITING FOR FILL]  timeout={timeout_s}s")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        o = client.get_order_by_id(order_id)
        status = o.status.value if hasattr(o.status, "value") else str(o.status)
        if status in ("filled", "partially_filled"):
            filled_avg = float(o.filled_avg_price) if o.filled_avg_price else 0
            print(f"  ✓ {status}  qty={o.filled_qty} @ avg ${filled_avg:.4f}  "
                  f"({ts()})")
            return True
        if status in ("rejected", "canceled", "expired"):
            print(f"  ✗ {status}  (terminal — won't fill)")
            return False
        time.sleep(1)
    print(f"  ⚠ timed out after {timeout_s}s — order still {status}")
    return False

def countdown(minutes: float) -> None:
    total_s = int(minutes * 60)
    start = time.time()
    while True:
        elapsed = int(time.time() - start)
        remaining = total_s - elapsed
        if remaining <= 0:
            break
        m, s = divmod(remaining, 60)
        print(f"\r  holding... {m:02d}:{s:02d} remaining", end="", flush=True)
        time.sleep(1)
    print(f"\r  hold complete                          ")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="SPY", help="Symbol to trade (default SPY)")
    p.add_argument("--shares", type=int, default=1, help="Share quantity (default 1)")
    p.add_argument("--hold",   type=float, default=30, help="Hold minutes (default 30)")
    p.add_argument("--yes",    action="store_true", help="Skip confirmation prompt")
    args = p.parse_args()

    api_key = os.environ.get("ALPACA_API_KEY")
    sec_key = os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("ALPACA_API_SECRET")
    base_url = os.environ.get("ALPACA_BASE_URL", "")
    if not api_key or not sec_key:
        sys.exit("Missing ALPACA_API_KEY / ALPACA_SECRET_KEY in environment")

    is_paper = "paper" in base_url
    if not is_paper:
        sys.exit("REFUSING: ALPACA_BASE_URL is not paper-api. This script only runs against paper accounts.")

    print()
    hr("═")
    print(f"  ALPACA SMOKE TEST TRADE — PAPER")
    hr("═")
    print(f"  symbol:   {args.symbol}")
    print(f"  shares:   {args.shares}")
    print(f"  hold:     {args.hold:.0f} minutes")
    print(f"  endpoint: {base_url}")

    if not args.yes:
        ans = input("\n  Proceed? [y/N]: ").strip().lower()
        if ans != "y":
            print("  cancelled")
            return 1

    client = TradingClient(api_key=api_key, secret_key=sec_key, paper=True)

    # ── BEFORE ───────────────────────────────────────────────────────────────
    hr()
    snap_before = show_account(client, "BEFORE")
    show_position(client, args.symbol)

    # ── BUY ──────────────────────────────────────────────────────────────────
    hr()
    buy_id = submit_market(client, args.symbol, args.shares, OrderSide.BUY)
    if not wait_for_fill(client, buy_id):
        print("\n✗ BUY did not fill — aborting hold")
        return 2

    # ── HOLD ─────────────────────────────────────────────────────────────────
    hr()
    print(f"\n[HOLDING POSITION]  {args.hold:.0f} min  ({ts()})")
    show_position(client, args.symbol)
    print()
    countdown(args.hold)

    # ── SELL ─────────────────────────────────────────────────────────────────
    hr()
    show_position(client, args.symbol)
    sell_id = submit_market(client, args.symbol, args.shares, OrderSide.SELL)
    wait_for_fill(client, sell_id)

    # ── AFTER ────────────────────────────────────────────────────────────────
    hr()
    snap_after = show_account(client, "AFTER")
    show_position(client, args.symbol)

    # ── REPORT ───────────────────────────────────────────────────────────────
    hr("═")
    eq_diff   = snap_after["equity"]   - snap_before["equity"]
    cash_diff = snap_after["cash"]     - snap_before["cash"]
    print(f"\n[REPORT]")
    print(f"  equity Δ:  {'+' if eq_diff >= 0 else ''}${eq_diff:.2f}")
    print(f"  cash Δ:    {'+' if cash_diff >= 0 else ''}${cash_diff:.2f}")
    print(f"  realized P&L (round trip, less fees): ${eq_diff:.2f}")

    # Show the orders that were placed in this run
    print(f"\n[ORDER HISTORY — last 4]")
    req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=4)
    for o in client.get_orders(req):
        filled = float(o.filled_avg_price) if o.filled_avg_price else 0
        print(f"  {o.submitted_at.strftime('%H:%M:%S')} {o.side.value.upper():4s} "
              f"{o.symbol} qty={o.filled_qty} @ ${filled:.4f}  status={o.status.value}")

    hr("═")
    print(f"\n  ✓ smoke test complete\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
