# ============================
# Stage 1: Frontend bauen
# ============================
FROM node:22-alpine AS frontend-build

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

# Zentrale Versionsdatei (wird von config.py gelesen)
COPY VERSION ./

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

# Uploads-Ordner sicherstellen (damit das Volume vom richtigen User beschreibbar ist)
RUN mkdir -p /app/app/static_new/uploads

# ============================
# Non-root User anlegen
# ============================
# Wenn der Container kompromittiert wird, beschränkt das den Schaden:
# Der Prozess kann weder System-Dateien manipulieren noch root-Container-Privilegien missbrauchen.
RUN groupadd --system --gid 1001 dnsmgr \
    && useradd --system --uid 1001 --gid dnsmgr --no-create-home --shell /usr/sbin/nologin dnsmgr \
    && chown -R dnsmgr:dnsmgr /app

USER dnsmgr

# Port
EXPOSE 8000

# Run
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
