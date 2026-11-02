#!/usr/bin/env bash
set -euo pipefail

# The Render persistent disk is mounted at runtime; migrate only after it exists.
python manage.py migrate --noinput
exec honcho start
