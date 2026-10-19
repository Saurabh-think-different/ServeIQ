FROM python:3.14.8-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83

COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:${PATH} \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/opt/dataset \
    DATABASE_PATH=/var/lib/servewell/servewell.db \
    QDRANT_URL=http://qdrant:6333

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY microservices/backend/pyproject.toml microservices/backend/README.md ./microservices/backend/
COPY microservices/backend/src/phdata ./microservices/backend/src/phdata

RUN uv sync --frozen --package servewell-backend --no-editable --no-dev --no-default-groups --no-cache \
    && useradd --uid 10001 --user-group --no-create-home appuser \
    && mkdir -p /var/lib/servewell \
    && chown 10001:10001 /var/lib/servewell

COPY client_data/data/ /opt/dataset/data/
COPY client_data/kb/ /opt/dataset/kb/
COPY client_data/tickets/train/ /opt/dataset/tickets/train/
COPY client_data/tickets/train_index.csv /opt/dataset/tickets/train_index.csv

VOLUME ["/var/lib/servewell"]
EXPOSE 8000
USER 10001:10001
CMD ["servewell-api"]
