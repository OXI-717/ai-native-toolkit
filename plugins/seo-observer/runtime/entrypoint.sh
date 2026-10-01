#!/usr/bin/env bash
# Tenant runtime entrypoint: render the crontab, run supercronic and the
# exports/health HTTP server. If either process dies, the container exits so
# `restart: unless-stopped` brings it back up.
set -euo pipefail

if [ -z "${SEO_OBSERVER_CONFIG:-}" ] || [ ! -f "$SEO_OBSERVER_CONFIG" ]; then
  echo "seo-observer runtime: SEO_OBSERVER_CONFIG must point to the tenant project.toml" >&2
  exit 64
fi

export SEO_OBSERVER_HOME="${SEO_OBSERVER_HOME:-/data}"
export TZ="${TZ:-UTC}"

# The namespace comes from the tenant config, not from deploy env, so the
# backup job always targets the same database the collector writes.
SEO_OBSERVER_NAMESPACE="$(python -c '
import sys
from pathlib import Path
from seo_observer.config import load_project_config
print(load_project_config(Path(sys.argv[1])).project.namespace)
' "$SEO_OBSERVER_CONFIG")"
export SEO_OBSERVER_NAMESPACE

template=/opt/runtime/crontab.default
if [ -f /config/crontab ]; then
  template=/config/crontab
fi
python /opt/runtime/render_crontab.py "$template" /tmp/crontab

supercronic -passthrough-logs /tmp/crontab &
cron_pid=$!

seo-observer serve --exports /data/exports --state-file /data/state/collect.json --host 0.0.0.0 --port 8080 &
serve_pid=$!

trap 'kill -TERM "$cron_pid" "$serve_pid" 2>/dev/null || true; wait' TERM INT

status=0
wait -n "$cron_pid" "$serve_pid" || status=$?
kill -TERM "$cron_pid" "$serve_pid" 2>/dev/null || true
wait || true
exit "$status"
