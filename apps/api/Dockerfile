# syntax=docker/dockerfile:1
# Build from the repository root: docker build -f apps/api/Dockerfile .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/srv/.venv/bin:$PATH"

WORKDIR /srv

# Dependencies first, so source changes don't reinstall them.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY packages/contracts/pyproject.toml packages/contracts/pyproject.toml
RUN uv sync --frozen --no-dev --package meeting-copilot-api --no-install-workspace

COPY apps/api apps/api
RUN uv sync --frozen --no-dev --package meeting-copilot-api

RUN useradd --system --no-create-home app
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
