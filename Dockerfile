FROM python:3.11-slim

# Set timezone to Asia/Jakarta (or your preferred timezone)
ENV TZ=Asia/Jakarta
RUN apt-get update && apt-get install -y tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Ensure data directory exists
RUN mkdir -p /app/data

# Run the scalper
CMD ["python", "-u", "main.py"]
