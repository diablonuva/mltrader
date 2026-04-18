#!/bin/bash
set -e
echo "Running walk-forward backtest..."
python -m src.backtest.cli --asset "${1:-SPY}" --start "${2:-2023-01-01}" --end "${3:-2024-01-01}"
