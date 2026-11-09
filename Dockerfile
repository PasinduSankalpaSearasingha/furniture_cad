FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_HTTP_TIMEOUT=120 UV_HTTP_RETRIES=5 PATH="/opt/venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxrender1 libxext6 libsm6 libgomp1 libxi6 libxfixes3 libxkbcommon0 libxxf86vm1 libxrandr2 libegl1 libopengl0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/furniture
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
COPY app ./app
COPY --from=frontend /build/app/studio_dist ./app/studio_dist
COPY third_party ./third_party
COPY blender ./blender
COPY migrations ./migrations
COPY alembic.ini ./
RUN useradd --uid 10001 --create-home furniture && mkdir -p /srv/artifacts /srv/furniture/data \
    && chown -R furniture:furniture /srv/artifacts /srv/furniture /opt/venv
USER furniture
ENV STORAGE_ROOT=/srv/artifacts
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
