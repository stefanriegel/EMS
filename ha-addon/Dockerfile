ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.21
FROM ${BUILD_FROM}

# ---------- build-time args ----------
ARG BUILD_ARCH
ARG BUILD_VERSION

# ---------- system packages ----------
# build-base + libffi-dev: bcrypt C extension on Alpine musl (K030)
# jq: parse /data/options.json in run.sh
# gfortran + openblas-dev: numpy/scipy/sklearn wheel build on aarch64
RUN apk add --no-cache \
    build-base \
    libffi-dev \
    gfortran \
    openblas-dev \
    jq

# ---------- Python dependencies ----------
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# ---------- application source ----------
COPY backend/ backend/
COPY dist/ frontend/dist/

# ---------- entrypoint ----------
COPY run.sh /run.sh
RUN chmod +x /run.sh

# ---------- labels ----------
LABEL \
    io.hass.name="EMS" \
    io.hass.description="Energy Management System for Huawei + Victron" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}"

EXPOSE 8000
ENTRYPOINT ["/run.sh"]
