#!/usr/bin/env bash
# Aegis Exchange — Demo Startup & Seed Script
set -e

API_URL="${AEGIS_API_URL:-http://localhost:8000}"

echo "=========================================================="
echo "Starting Aegis Exchange Demo Stack"
echo "=========================================================="

# Check if Docker Compose is available
if command -v docker >/dev/null 2>&1; then
    echo "Starting API and Dashboard services via Docker Compose..."
    docker compose up -d api dashboard

    echo "Waiting for API health..."
    until curl -s "${API_URL}/health" | grep -q "ok"; do
        sleep 1
    done
    echo "API is healthy at ${API_URL}"
else
    echo "Docker not detected. Assuming API is running locally at ${API_URL}..."
fi

echo "Injecting sample scam typologies via simulator..."
python3 -m simulator.main burst --typology mule_fan_in --api-url "${API_URL}" || true
python3 -m simulator.main burst --typology rapid_forwarding --api-url "${API_URL}" || true

echo "Demo stack seeded! Access the dashboard at http://localhost:8501"
