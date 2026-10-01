# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    SEO_OBSERVER_HOME=/data

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
       age rclone sqlite3 ca-certificates curl tzdata procps \
    && rm -rf /var/lib/apt/lists/*

# supercronic: pinned release and per-arch checksum, verified with sha1sum.
# https://github.com/aptible/supercronic/releases
ARG SUPERCRONIC_REPO=aptible/supercronic
ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA1SUM_AMD64=e63c11a9726b775a6a11801e81af4f3fb926aa68
ARG SUPERCRONIC_SHA1SUM_ARM64=0b6c5bb743e0b0dafed1132198c81807927ac413
ARG TARGETARCH
RUN set -eu; \
    case "$TARGETARCH" in \
        amd64) SUPERCRONIC_SHA1SUM="$SUPERCRONIC_SHA1SUM_AMD64" ;; \
        arm64) SUPERCRONIC_SHA1SUM="$SUPERCRONIC_SHA1SUM_ARM64" ;; \
        *) echo "unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo supercronic \
        "https://github.com/${SUPERCRONIC_REPO}/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
    && echo "${SUPERCRONIC_SHA1SUM}  supercronic" | sha1sum -c - \
    && chmod +x supercronic \
    && mv supercronic /usr/local/bin/supercronic

COPY cli/ /opt/seo-observer/
RUN pip install --no-cache-dir "/opt/seo-observer[pdf]" \
    && playwright install-deps chromium \
    && playwright install chromium

COPY runtime/ /opt/runtime/
RUN chmod +x /opt/runtime/entrypoint.sh /opt/runtime/render_crontab.py \
    && groupadd --gid 10001 observer \
    && useradd --uid 10001 --gid observer --shell /usr/sbin/nologin observer \
    && mkdir -p /data /config \
    && chown -R observer:observer /data /config

USER observer

# Documentation only: the deployment example publishes no ports; the only way
# in is the cloudflared service on the internal network.
EXPOSE 8080

# Liveness, not data freshness: a stale source must never restart a healthy
# container (data alerting is the job of the external /health poller).
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/health | grep -Eq '^(200|503)$'

ENTRYPOINT ["/opt/runtime/entrypoint.sh"]
