ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.12-alpine3.21
FROM ${BUILD_FROM}

# Build context must be the project root:
#   docker build --platform linux/amd64 -t ems-addon-test \
#     --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.21 \
#     -f ha-addon/Dockerfile .
# The HA Builder passes --build-arg BUILD_FROM automatically from build.yaml.

WORKDIR /app

# Install build toolchain for bcrypt C extension on Alpine musl (K030).
# Without build-base + libffi-dev, pip install passlib[bcrypt] fails with
# "gcc: not found" on Alpine's musl libc.
RUN apk add --no-cache build-base libffi-dev

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY backend/ backend/
COPY frontend/dist frontend/dist
COPY ha-addon/run.sh /run.sh
RUN chmod +x /run.sh

EXPOSE 8000
CMD ["/run.sh"]
