#!/usr/bin/env bash
# Simple smoke test for MyFilmPeople
# Usage: HOST=https://example.com ./smoke_test.sh

HOST=${HOST:-http://localhost:8000}
set -euo pipefail

echo "Running smoke tests against $HOST"

# Check homepage
echo "Checking homepage..."
curl -fSL --max-time 10 "$HOST/" -o /dev/null

# Check search page
echo "Checking search page..."
curl -fSL --max-time 10 "$HOST/search/?q=tom" -o /dev/null

# Check person page (example TMDb id 525)
echo "Checking person page..."
curl -fSL --max-time 10 "$HOST/person/525/" -o /dev/null

# Check login page
echo "Checking login page..."
curl -fSL --max-time 10 "$HOST/accounts/login/" -o /dev/null

# Optional: check static assets
echo "Checking static asset..."
curl -fSL --max-time 10 "$HOST/static/css/base.css" -o /dev/null

echo "Smoke tests passed."
