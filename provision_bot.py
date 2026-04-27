#!/usr/bin/env python3
import os
import sys
import sqlite3
import subprocess
import secrets
import shutil
from werkzeug.security import generate_password_hash

def print_banner():
    print("=" * 50)
    print("   AlgoVibe SaaS - Tenant Provisioning System   ")
    print("=" * 50)

def find_free_port(start_port=8081):
    """Mencari port yang tersedia mulai dari start_port."""
    port = start_port
    while True:
        # Cek apakah port sudah digunakan oleh docker
        result = subprocess.run(
            f"docker ps --format '{{{{.Ports}}}}' | grep ':{port}->'",
            shell=True, capture_output=True, text=True
        )
        if not result.stdout:
            return port
        port += 1

def setup_tenant_db(tenant_path, username, password, api_key, api_secret):
    """Inisialisasi database khusus untuk tenant baru."""
    db_path = os.path.join(tenant_path, "algotrade.db")
    print(f"[*] Initializing database at {db_path}...")
    
    # Copy schema from main DB or just create fresh
    # Untuk SaaS, kita buat fresh dan jalankan query inisialisasi
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Jalankan schema minimal yang dibutuhkan (Users & Settings)
    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            api_key TEXT,
            api_secret TEXT,
            role TEXT DEFAULT 'USER',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT,
            side TEXT,
            message TEXT,
            level TEXT,
            price REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    
    # Tambahkan user admin pertama
    pw_hash = generate_password_hash(password)
    cursor.execute(
        "INSERT INTO users (username, password_hash, api_key, api_secret, role) VALUES (?, ?, ?, ?, 'ADMIN')",
        (username, pw_hash, api_key, api_secret)
    )
    
    conn.commit()
    conn.close()

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 provision_bot.py <username> <password> [api_key] [api_secret]")
        sys.exit(1)

    username = sys.argv[1].lower()
    password = sys.argv[2]
    api_key = sys.argv[3] if len(sys.argv) > 3 else ""
    api_secret = sys.argv[4] if len(sys.argv) > 4 else ""

    print_banner()

    # 1. Setup Path
    tenant_base = os.path.abspath(f"data/tenants/{username}")
    log_dir = os.path.join(tenant_base, "logs")
    
    if os.path.exists(tenant_base):
        print(f"[!] Tenant '{username}' already exists. Updating container...")
    else:
        os.makedirs(log_dir, exist_ok=True)

    # 2. Setup Database
    setup_tenant_db(tenant_base, username, password, api_key, api_secret)

    # 3. Find Port
    port = find_free_port()
    print(f"[*] Assigned Port: {port}")

    # 4. Generate Secret Key for this tenant
    secret_key = secrets.token_hex(32)

    # 5. Launch Docker Container
    print(f"[*] Launching Docker container: algovibe_{username}...")
    
    # Hapus container lama jika ada
    subprocess.run(f"docker rm -f algovibe_{username}", shell=True, capture_output=True)

    docker_cmd = [
        "docker", "run", "-d",
        "--name", f"algovibe_{username}",
        "--restart", "unless-stopped",
        "-p", f"{port}:8080",
        "-e", f"SECRET_KEY={secret_key}",
        "-e", f"INDODAX_API_KEY={api_key}",
        "-e", f"INDODAX_API_SECRET={api_secret}",
        "-e", f"APP_USERNAME={username}",
        "-v", f"{tenant_base}:/app/data",
        "-v", f"{os.path.abspath('models')}:/app/models:ro",
        "80.241.217.181:5000/algovibe:latest"
    ]

    result = subprocess.run(docker_cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        print("\n" + "=" * 50)
        print(f"✅ SUCCESS: Bot for '{username}' is LIVE!")
        print(f"🌐 Dashboard: http://80.241.217.181:{port}/mobile.html")
        print(f"🔑 Admin: {username}")
        print("=" * 50)
    else:
        print(f"❌ FAILED: {result.stderr}")

if __name__ == "__main__":
    main()
