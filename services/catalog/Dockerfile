FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# shared/ is path-imported (ADR-0013); service code lives under services/catalog/.
COPY shared/ /app/shared/
COPY services/catalog/pyproject.toml /app/

RUN uv sync --no-dev

COPY services/catalog/ /app/

# Apply migrations on startup, then serve.
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn main:app --host 0.0.0.0 --port 8000"]
