FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8001

EXPOSE 8001

CMD ["sh", "-c", "gunicorn api:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} --timeout 120 -b 0.0.0.0:${PORT:-8001}"]
