FROM python:3.13-slim

WORKDIR /app

# System dependencies for feedparser and aiohttp
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .
COPY src/ src/
COPY static/ static/

EXPOSE 8000

CMD ["python", "main.py"]
