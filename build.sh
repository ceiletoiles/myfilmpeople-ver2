#!/bin/bash
# Build script for Render.com deployment

set -o errexit

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Collect static files
python manage.py collectstatic --no-input

# Run database migrations
python manage.py migrate --noinput

# Ensure an admin account exists when the Render env vars are set
python manage.py bootstrap_admin
