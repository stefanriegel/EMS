ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG BUILD_VERSION

WORKDIR /app

# System deps: build tools for bcrypt/sklearn, Node.js for frontend build, jq for run.sh
RUN apk add --no-cache build-base libffi-dev gfortran openblas-dev jq nodejs npm

# Limit OpenMP/BLAS threads to avoid oversubscription in Docker containers
ENV OMP_NUM_THREADS=2
ENV OPENBLAS_NUM_THREADS=2

# Frontend build
COPY frontend/ frontend/
RUN cd frontend && npm ci && npm run build && rm -rf node_modules src

# Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Application source
COPY backend/ backend/

# Remove build-time tools (not needed at runtime)
RUN apk del nodejs npm

# Entrypoint
COPY run.sh /run.sh
RUN chmod +x /run.sh

LABEL \
    io.hass.name="EMS" \
    io.hass.description="Energy Management System for Huawei + Victron" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}"

EXPOSE 8000
ENTRYPOINT ["/run.sh"]
