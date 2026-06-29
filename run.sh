#!/usr/bin/env bash
# One-command launcher for the Stripe Docs Assistant.
# Requires only Docker Desktop — everything else runs inside containers.
set -e

if ! command -v docker >/dev/null 2>&1; then
  echo "❌ Docker is required but not installed."
  echo "   Install Docker Desktop: https://docs.docker.com/get-docker/"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker is installed but the daemon isn't running."
  echo "   Start Docker Desktop, wait for it to finish starting, then re-run ./run.sh"
  exit 1
fi

echo "🚀 Building and starting the Stripe Docs Assistant…"
echo "   • First run downloads ~2GB of local AI models — this can take a few minutes."
echo "   • When it's up, open  http://localhost:5055/  and click 'Load Stripe docs'."
echo "   • Press Ctrl+C to stop. (Run 'docker compose down' to remove the containers.)"
echo

exec docker compose up --build
