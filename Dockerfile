FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set working directory
WORKDIR /app

# Copy application code
COPY . .

# Expose port
EXPOSE 10000

# Run the application
CMD ["sh", "-c", "uvicorn app.api:app --host 0.0.0.0 --port ${PORT:-10000}"] 