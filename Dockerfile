# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MTW_DATA_DIR=/data \
    MTW_PORT=8318

WORKDIR /app

# Only the standard library is used, so no pip install step is needed.
COPY run.py ./
COPY app ./app
COPY web ./web

RUN mkdir -p /data/logs /data/plugins

VOLUME ["/data"]
EXPOSE 8318

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8318/api/health', timeout=4)" || exit 1

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8318"]
