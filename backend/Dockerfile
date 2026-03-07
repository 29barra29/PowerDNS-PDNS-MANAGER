# ============================
# Stage 1: Frontend bauen
# ============================
FROM node:20-alpine AS frontend-build

WORKDIR /frontend

# Dependencies installieren
COPY frontend/package*.json ./
RUN npm ci

# Frontend bauen
COPY frontend/ .
RUN npm run build

# ============================
# Stage 2: Backend + Frontend
# ============================
FROM python:3.12-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Backend code
COPY backend/app/ ./app/

# Frontend build vom ersten Stage kopieren
COPY --from=frontend-build /frontend/dist ./app/static_new/

# Port
EXPOSE 8000

# Run
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
