FROM python:3.11-slim

# Install system FFmpeg and tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application
COPY backend/ ./backend/
RUN mkdir -p /app/downloads && chmod 777 /app/downloads

ENV PYTHONUNBUFFERED=1

# Render sets the PORT environment variable (defaults to 10000)
ENV PORT=10000
EXPOSE 10000

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
