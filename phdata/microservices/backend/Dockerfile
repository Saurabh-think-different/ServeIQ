FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DATABASE_URL=sqlite:////app/data/phdata.db
COPY phdata/microservices/backend/pyproject.toml phdata/microservices/backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY phdata/microservices/backend/ .
COPY client_data/data/ /app/client_data/data/
COPY client_data/tickets/train/ /app/client_data/tickets/train/
COPY client_data/tickets/train_index.csv /app/client_data/tickets/train_index.csv
COPY client_data/kb/ /app/client_data/kb/
COPY client_data/labels/train_labels.json /app/client_data/labels/train_labels.json
RUN uv sync --frozen --no-dev \
    && mkdir -p /app/data \
    && useradd --uid 10001 --create-home app \
    && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uv", "run", "--frozen", "--no-dev", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
