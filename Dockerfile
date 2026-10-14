# MockTrade server image: the screens are built with Node, then served by the Python app.
#   docker build -t mocktrade .          (or: docker compose -f deploy/compose.yaml build)

# --- 1. the screens --------------------------------------------------------------------------
FROM node:22-slim AS screens
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

# --- 2. the server ---------------------------------------------------------------------------
FROM python:3.13-slim AS server
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin mocktrade \
    && mkdir /data && chown mocktrade /data
COPY --from=screens /ui/dist ./ui/dist

# no secrets here: they come from the environment at run time (deploy/.env)
ENV MOCKTRADE_HOST=0.0.0.0 \
    MOCKTRADE_PORT=8000 \
    MOCKTRADE_DATA=/data \
    MOCKTRADE_USERS_DB=/data/users.db \
    MOCKTRADE_UI=/app/ui/dist
USER mocktrade
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"]
STOPSIGNAL SIGTERM
CMD ["python", "-m", "mocktrade.tools.serve"]
