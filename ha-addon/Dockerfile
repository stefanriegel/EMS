ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.12-alpine3.21
FROM ${BUILD_FROM}

# The HA Supervisor uses the ha-addon/ directory as Docker build context.
# Source code (backend/, pyproject.toml) lives in the parent repo.
# We install from the GitHub repo URL directly during the build,
# then copy the pre-built frontend dist from this directory.

ARG BUILD_VERSION=0.3.0

WORKDIR /app

# Install build toolchain for bcrypt C extension on Alpine musl (K030).
RUN apk add --no-cache build-base libffi-dev jq git

# Install the EMS backend from the GitHub repository.
# The pip install fetches pyproject.toml + backend/ from the repo.
RUN pip install --no-cache-dir \
    "ems @ git+https://github.com/stefanriegel/EMS.git@main"

# Copy the pre-built frontend dist (committed to ha-addon/ by deploy script)
# If dist/ doesn't exist, create an empty placeholder so the COPY doesn't fail.
COPY dist/ frontend/dist/

COPY run.sh /run.sh
RUN chmod +x /run.sh

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="addon" \
  io.hass.arch="aarch64|amd64"

EXPOSE 8000
# Use ENTRYPOINT to bypass s6-overlay (K057)
ENTRYPOINT ["/run.sh"]
