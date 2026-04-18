#!/bin/bash
set -e
echo "=== ML Trader Diablo v1 — Deploy ==="
echo "Checking .env..."
[ ! -f .env ] && { echo "ERROR: .env not found. Copy .env.example and fill in keys."; exit 1; }
echo "Building Docker images..."
docker-compose build
echo "Starting services..."
docker-compose up -d
echo "Tailing logs (Ctrl+C to stop)..."
docker-compose logs -f trader
