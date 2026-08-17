#!/bin/bash
# Start PostgreSQL via Docker, then run API locally

set -e

echo "Starting PostgreSQL..."

docker compose up -d db

echo ""
echo "Waiting for PostgreSQL to be ready..."
sleep 3

echo ""
echo "Starting API locally on http://localhost:8001"
echo ""
echo "Press Ctrl+C to stop everything"
echo ""

# Export env vars for local API
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/voyager
export PORT=8001

# Run API (will exit on Ctrl+C, then clean up docker)
trap 'echo ""; echo "Stopping services..."; docker compose down; exit 0' INT TERM

python api.py
