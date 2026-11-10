FROM python:3.12-slim

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY bridge.py /app/bridge.py

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD ["python3", "/app/bridge.py", "status"]

ENTRYPOINT ["python3", "/app/bridge.py"]
CMD ["loop"]
