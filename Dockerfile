FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY patchwork/ ./patchwork/
COPY dummy_repo/ ./dummy_repo/
COPY scripts/ ./scripts/

EXPOSE 8000

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "patchwork.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
