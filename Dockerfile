FROM node:22-alpine AS web-build

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TALLYGUARD_FRONTEND_DIST=/app/web/dist \
    TALLYGUARD_RELIABILITY_REPORT=/app/docs/reports/load-test-10000.json \
    TALLYGUARD_AGENT_RELIABILITY_REPORT=/app/docs/reports/agent-run-load-50.json \
    TALLYGUARD_DATABASE_PATH=/tmp/tallyguard.sqlite3 \
    TALLYGUARD_MODE=simulation \
    TALLYGUARD_ARC_NETWORK=ARC-TESTNET \
    TALLYGUARD_ALLOW_MAINNET=false \
    TALLYGUARD_MAX_TRANSFER_USDC=5.00 \
    TALLYGUARD_MAX_EVIDENCE_BYTES=10485760

WORKDIR /app

RUN groupadd --system tallyguard \
    && useradd --system --gid tallyguard --home-dir /app tallyguard

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

COPY --from=web-build /build/web/dist ./web/dist
COPY docs/reports/load-test-10000.json ./docs/reports/load-test-10000.json
COPY docs/reports/agent-run-load-50.json ./docs/reports/agent-run-load-50.json
COPY docs/reports/arc-testnet-public-activity.json ./docs/reports/arc-testnet-public-activity.json
RUN chown -R tallyguard:tallyguard /app

USER tallyguard
EXPOSE 8000

# One process is intentional until transient demo sessions and approval state are
# moved to a shared durable store. Threads still allow concurrent judge traffic.
CMD ["sh", "-c", "exec gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT:-8000} --access-logfile - --error-logfile - 'tallyguard.api:create_app()'"]
