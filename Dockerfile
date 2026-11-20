FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ULTRON_ENV=production \
    ULTRON_DATABASE_PATH=/data/ultron.db

WORKDIR /app
COPY pyproject.toml README.md ./
COPY ultron ./ultron
COPY scripts ./scripts
RUN pip install --no-cache-dir .
RUN mkdir -p /data && chown -R 10001:10001 /app /data
USER 10001:10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["sh", "-c", "uvicorn ultron.api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
