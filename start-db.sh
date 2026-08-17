#!/bin/bash
# Start PostgreSQL only (no API)

set -e

echo "Starting PostgreSQL..."

docker compose up -d db

echo ""
echo "Waiting for PostgreSQL to be ready..."
sleep 3

echo ""
echo "PostgreSQL: postgresql://postgres:postgres@localhost:5432/voyager"
echo ""
echo "Run ./start.sh later to also start the API locally."
