# Stage 1: Frontend build
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime (HA Add-on base)
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.21
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG BUILD_VERSION

WORKDIR /app

# System deps: build-base + libffi-dev for bcrypt, gfortran + openblas-dev for sklearn, jq for run.sh
RUN apk add --no-cache build-base libffi-dev gfortran openblas-dev jq

# Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Application source
COPY backend/ backend/
COPY --from=frontend-build /app/frontend/dist frontend/dist

# Entrypoint
COPY ha-addon/run.sh /run.sh
RUN chmod +x /run.sh

LABEL \
    io.hass.name="EMS" \
    io.hass.description="Energy Management System for Huawei + Victron" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}"

EXPOSE 8000
ENTRYPOINT ["/run.sh"]
