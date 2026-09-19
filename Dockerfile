# Use official Python runtime as base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set timezone to Eastern
ENV TZ=America/New_York

# Install system dependencies (curl for healthcheck, tzdata for timezone)
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application files (static files needed for Flask)
COPY api.py .
COPY gtfs_static.py .
COPY gtfs ./gtfs
COPY index.html .
COPY app.js .
COPY style.css .
COPY manifest.json .
COPY service-worker.js .

# Expose port
EXPOSE 5001

# Set environment variables
ENV FLASK_APP=api.py
ENV FLASK_ENV=production

# Health check using curl (lighter than Python)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5001/health || exit 1

# Run with gunicorn for production
CMD ["gunicorn", "api:app", "--bind", "0.0.0.0:5001", "--workers", "2", "--timeout", "30"]
