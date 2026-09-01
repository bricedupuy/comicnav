FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/huggingface \
    HOME=/app \
    PYTHONUSERBASE=/models/python \
    XDG_CACHE_HOME=/models/.cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY models.yaml ./models.yaml
COPY app ./app
COPY web ./web

RUN mkdir -p /models/python /models/.cache \
    && chown -R 10001:10001 /models /app
USER 10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
