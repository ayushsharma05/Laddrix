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
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
# exactly the reviewed versions, each checked against its hash (#365); then our own code
RUN pip install --require-hashes -r requirements.lock \
    && pip install --no-deps . \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin mocktrade \
    && mkdir /data && chown mocktrade /data
COPY --from=screens /ui/dist ./ui/dist

# the commit this image is built from (deploy/deploy.sh passes it), shown by /api/health
ARG MOCKTRADE_BUILD=unknown
# no secrets here: they come from the environment at run time (deploy/.env)
ENV MOCKTRADE_BUILD=$MOCKTRADE_BUILD \
    MOCKTRADE_HOST=0.0.0.0 \
    MOCKTRADE_PORT=8000 \
    MOCKTRADE_DATA=/data \
    MOCKTRADE_USERS_DB=/data/users.db \
    MOCKTRADE_UI=/app/ui/dist
USER mocktrade
VOLUME /data
EXPOSE 8000
# healthy = ready (#287): no part starting or down (GET /api/ready, src/mocktrade/ops/status.py).
# A restart's recovery may ask the broker for a while: the start period allows for it.
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/ready', timeout=2)"]
STOPSIGNAL SIGTERM
CMD ["python", "-m", "mocktrade.tools.serve"]
