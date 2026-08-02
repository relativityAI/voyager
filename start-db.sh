#!/bin/bash
# Start MongoDB and Mongo Express only (no API)

set -e

echo "Starting MongoDB and Mongo Express..."

docker compose up -d db mongo-express

echo ""
echo "Waiting for MongoDB to be ready..."
sleep 3

echo ""
echo "MongoDB: mongodb://root:example@localhost:27017/"
echo "Mongo Express: http://localhost:8081 (user: mongoexpressuser, pass: mongoexpresspass)"
echo ""
echo "Run ./start.sh later to also start the API locally."
