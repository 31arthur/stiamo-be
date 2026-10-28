# Build from the repository root:  docker build -f services/files/Dockerfile -t stiamo-files .
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY libs/stiamo_common libs/stiamo_common
COPY services/files services/files
RUN python -m venv /opt/venv  && /opt/venv/bin/pip install ./libs/stiamo_common ./services/files

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
COPY --from=builder /opt/venv /opt/venv
# alembic.ini + migrations are read from the working directory
COPY services/files/alembic.ini /srv/alembic.ini
COPY services/files/migrations /srv/migrations
COPY services/files/docker-entrypoint.sh /srv/docker-entrypoint.sh
WORKDIR /srv
RUN useradd --system --no-create-home app && chmod +x docker-entrypoint.sh
RUN mkdir -p /data/files && chown app /data/files
ENV FILES_STORAGE_DIR=/data/files
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5   CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"
ENTRYPOINT ["./docker-entrypoint.sh"]
