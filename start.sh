#!/bin/bash
# Start MongoDB and Mongo Express via Docker, then run API locally

set -e

echo "Starting MongoDB and Mongo Express..."

docker compose up -d db mongo-express

echo ""
echo "Waiting for MongoDB to be ready..."
sleep 3

echo ""
echo "Starting API locally on http://localhost:8001"
echo "Mongo Express: http://localhost:8081 (user: mongoexpressuser, pass: mongoexpresspass)"
echo ""
echo "Press Ctrl+C to stop everything"
echo ""

# Export env vars for local API
export MONGODB_URL=mongodb://root:example@localhost:27017/
export MONGODB_DB_NAME=voyager
export PORT=8001

# Run API (will exit on Ctrl+C, then clean up docker)
trap 'echo ""; echo "Stopping services..."; docker compose down; exit 0' INT TERM

python api.py