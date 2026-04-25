FROM python:3.12-slim

# Timezone
ENV TZ=Asia/Jakarta
RUN apt-get update && apt-get install -y tzdata curl ca-certificates && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Pastikan direktori data & log ada
RUN mkdir -p /app/data/logs

# Beri permission ke startup script
RUN chmod +x /app/start.sh

# Jalankan bot + dashboard sekaligus
CMD ["/app/start.sh"]
