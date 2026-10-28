# Build from the repository root:  docker build -f services/records/Dockerfile -t stiamo-records .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY libs/stiamo_common /srv/libs/stiamo_common
COPY services/records /srv/service
RUN pip install --no-cache-dir /srv/libs/stiamo_common /srv/service
WORKDIR /srv/service
RUN useradd --system --no-create-home app && chown -R app /srv
USER app
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000"]
