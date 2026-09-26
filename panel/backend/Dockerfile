# Build context: repository root (the image ships client/server_scripts, shared with the Qt client).
#   docker build -f panel/backend/Dockerfile -t amnezia-panel-backend .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PANEL_SCRIPTS_DIR=/app/server_scripts \
    PATH=/app/.venv/bin:$PATH

RUN pip install --no-cache-dir uv==0.8.* \
    && useradd --system --home /app --shell /usr/sbin/nologin panel

WORKDIR /app
COPY panel/backend/pyproject.toml panel/backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY panel/backend/app ./app
COPY client/server_scripts ./server_scripts

USER panel
EXPOSE 8000
CMD ["sh", "-c", "python -m app.cli migrate && exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
