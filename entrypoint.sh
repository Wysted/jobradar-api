#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting Scraping Masive API on port ${PORT:-8000}..."
uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
