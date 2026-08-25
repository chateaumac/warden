FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    WARDEN_DATA_DIR=/data

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt pyproject.toml README.md ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

COPY app/ app/
COPY profiles/ profiles/
COPY scripts/ scripts/
COPY tests/ tests/

VOLUME ["/data"]
EXPOSE 8484

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8484/healthz', timeout=4); sys.exit(0 if r.status==200 else 1)"

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8484"]
