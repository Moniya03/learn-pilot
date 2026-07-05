FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# shared/ is path-imported (ADR-0013); service code lives under services/identity/.
COPY shared/ /app/shared/
COPY services/identity/pyproject.toml services/identity/uv.lock /app/

RUN uv sync --frozen --no-dev

COPY services/identity/ /app/

# Apply migrations on startup, then serve.
CMD ["sh", "-c", "uv run python -m migrate && uv run uvicorn main:app --host 0.0.0.0 --port 8000"]