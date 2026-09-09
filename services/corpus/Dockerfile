# GalaxyHire corpus backend — one image, two run roles (api | worker).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# install deps first for layer caching
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

# app source
COPY . .
RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "galaxy.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
