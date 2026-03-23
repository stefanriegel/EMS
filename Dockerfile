FROM python:3.12-slim
WORKDIR /app

# Limit OpenMP/BLAS threads to avoid oversubscription in Docker containers
# (sklearn/numpy default to nproc which sees host CPUs, not cgroup limits)
ENV OMP_NUM_THREADS=2
ENV OPENBLAS_NUM_THREADS=2

COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY backend/ backend/
COPY frontend/dist frontend/dist
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
