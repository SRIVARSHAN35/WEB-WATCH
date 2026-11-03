#!/usr/bin/env bash
set -euo pipefail

# Never mutate the configured database as a side effect of a service restart.
# Fail early and visibly if a separately reviewed migration has not been applied.
python manage.py check
python manage.py migrate --check
exec honcho start
