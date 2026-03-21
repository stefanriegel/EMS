ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.12-alpine3.21
FROM ${BUILD_FROM}

# Build context is the project root (set by GitHub Actions or manual build):
#   docker build --platform linux/amd64 -t ems-addon-test \
#     --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.21 \
#     -f ha-addon/Dockerfile .
# The GitHub Actions workflow passes --build-arg BUILD_FROM per architecture.

WORKDIR /app

# Install build toolchain for bcrypt C extension on Alpine musl (K030).
# Without build-base + libffi-dev, pip install passlib[bcrypt] fails with
# "gcc: not found" on Alpine's musl libc.
RUN apk add --no-cache build-base libffi-dev jq

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY backend/ backend/
COPY frontend/dist frontend/dist
COPY ha-addon/run.sh /run.sh
RUN chmod +x /run.sh

LABEL \
  io.hass.version="0.3.0" \
  io.hass.type="addon" \
  io.hass.arch="aarch64|amd64"

EXPOSE 8000
# Use ENTRYPOINT to bypass s6-overlay (K057)
ENTRYPOINT ["/run.sh"]
