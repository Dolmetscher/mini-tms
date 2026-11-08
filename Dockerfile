# Mini-TMS одним контейнером: многоступенчатая сборка.
# Этап 1 — сборка SPA (web/dist), этап 2 — рантайм FastAPI, который раздает
# и API, и статику одним URL (см. main.py: TMS_WEB_DIR).

# --- Этап 1: фронтенд ---
FROM node:20-alpine AS webbuild
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Этап 2: бэкенд + статика ---
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TMS_WEB_DIR=/app/web/dist \
    TMS_DATA_DIR=/data \
    TMS_DATABASE_URL=sqlite:////data/tms.db

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=webbuild /build/dist ./web/dist

# БД SQLite и загруженные документы живут в томе /data — переживают
# пересоздание контейнера и бэкапятся одной командой.
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
