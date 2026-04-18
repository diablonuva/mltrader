FROM python:3.11-slim-bullseye
LABEL maintainer="diablonuva"
LABEL description="ML Trader Diablo v1 — HMM + LightGBM intraday trading system"

# System dependencies for LightGBM on ARM64
RUN apt-get update && apt-get install -y \
    libgomp1 \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY config/ ./config/
COPY main.py .

# Create runtime directories (will be overridden by bind mounts)
RUN mkdir -p logs models data

# Health check — verify Python imports succeed
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "from src.config_loader import load_config; load_config()" || exit 1

# Drop root privileges
RUN useradd --system --no-create-home trader
USER trader

CMD ["python", "main.py"]
