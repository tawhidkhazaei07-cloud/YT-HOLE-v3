FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YTHOLE_HOST=0.0.0.0 \
    YTHOLE_SERVE_FRONTEND=true \
    YTHOLE_DAILY_DOWNLOAD_LIMIT=10

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

RUN mkdir -p /app/data /app/logs
EXPOSE 10000

CMD ["sh", "-c", "export YTHOLE_PORT=${PORT:-10000}; exec python -m backend"]
