#!/usr/bin/env bash
# Bu Nurbek — one-shot installer for an existing Ubuntu 22+ Droplet.
# Safe: idempotent, never touches existing nginx server blocks, isolates everything under /opt/bunurbek.
#
# Usage (run on the Droplet as root):
#   curl -fsSL https://raw.githubusercontent.com/bunurbek/platform/main/deploy/install.sh | sudo bash
#
# Optional env vars (set before piping):
#   SECRET_KEY            (auto-generated if missing)
#   TELEGRAM_BOT_TOKEN    (REQUIRED — prompted if missing)
#   TELEGRAM_BOT_USERNAME (default: bunurbekauth_bot)
#   DOMAIN                (default: skip — uses :8001 on Droplet IP)
#   SITE_URL              (auto-derived from DOMAIN; falls back to http://IP:8001)

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
APP_NAME="bunurbek"
APP_USER="bunurbek"
APP_DIR="/opt/bunurbek"
REPO_URL="https://github.com/bunurbek/platform.git"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WEB_PORT="${WEB_PORT:-8001}"
TELEGRAM_BOT_USERNAME="${TELEGRAM_BOT_USERNAME:-bunurbekauth_bot}"
DOMAIN="${DOMAIN:-skip}"

# ── Colors ────────────────────────────────────────────────────────────────────
say() { printf "\n\033[1;33m▶ %s\033[0m\n" "$*"; }
ok()  { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
err() { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; }

# ── Sanity ────────────────────────────────────────────────────────────────────
[[ "$(id -u)" -eq 0 ]] || { err "Run as root (or with sudo)"; exit 1; }

DROPLET_IP=$(curl -fsSL https://api.ipify.org || echo "YOUR_IP")
say "Droplet IP: $DROPLET_IP"

# ── 1. System packages ───────────────────────────────────────────────────────
say "Updating package index + installing deps"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip python3-dev build-essential \
    postgresql postgresql-contrib libpq-dev \
    git nginx curl ufw \
    ffmpeg \
    >/dev/null
ok "Deps installed"

# ── 2. App user + dir ────────────────────────────────────────────────────────
if ! id "$APP_USER" >/dev/null 2>&1; then
    say "Creating app user '$APP_USER'"
    adduser --system --group --home "$APP_DIR" --shell /bin/bash "$APP_USER"
    ok "User created"
fi

# ── 3. Clone / update repo ───────────────────────────────────────────────────
if [[ -d "$APP_DIR/.git" ]]; then
    say "Pulling latest from GitHub"
    sudo -u "$APP_USER" git -C "$APP_DIR" pull --rebase
else
    say "Cloning repo to $APP_DIR"
    rm -rf "$APP_DIR"
    sudo -u "$APP_USER" git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
ok "Repo ready"

# ── 4. Python venv + deps ────────────────────────────────────────────────────
say "Setting up Python venv"
sudo -u "$APP_USER" $PYTHON_BIN -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip wheel >/dev/null
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" >/dev/null
ok "Python deps installed"

# ── 5. PostgreSQL database ───────────────────────────────────────────────────
say "Ensuring Postgres user + DB exist"
DB_NAME="${APP_NAME}_db"
DB_USER="${APP_NAME}_user"
DB_PASS="${DB_PASS:-$(openssl rand -base64 32 | tr -d '=+/' | head -c 32)}"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 || \
    sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
ok "Postgres ready: $DB_NAME owned by $DB_USER"

# ── 6. .env file ─────────────────────────────────────────────────────────────
ENV_FILE="$APP_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    say "Creating .env"
    SECRET_KEY="${SECRET_KEY:-$($PYTHON_BIN -c 'import secrets;print(secrets.token_urlsafe(64))')}"

    if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
        echo ""
        echo "  Paste your TELEGRAM_BOT_TOKEN from BotFather (input hidden):"
        read -rs TELEGRAM_BOT_TOKEN
        echo ""
    fi

    # Determine SITE_URL & ALLOWED_HOSTS
    if [[ "$DOMAIN" != "skip" && -n "$DOMAIN" ]]; then
        SITE_URL="https://$DOMAIN"
        ALLOWED="$DOMAIN,$DROPLET_IP,localhost,127.0.0.1"
        CSRF_ORIG="https://$DOMAIN"
    else
        SITE_URL="http://$DROPLET_IP:$WEB_PORT"
        ALLOWED="$DROPLET_IP,localhost,127.0.0.1"
        CSRF_ORIG="http://$DROPLET_IP:$WEB_PORT"
    fi

    cat > "$ENV_FILE" <<EOF
SECRET_KEY=$SECRET_KEY
DEBUG=False
ALLOWED_HOSTS=$ALLOWED
CSRF_TRUSTED_ORIGINS=$CSRF_ORIG
SITE_URL=$SITE_URL

DATABASE_URL=postgres://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}

TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
TELEGRAM_BOT_USERNAME=$TELEGRAM_BOT_USERNAME
MODULE_COOLDOWN_SECONDS=86400

USE_R2=False
EOF
    chmod 600 "$ENV_FILE"
    chown "$APP_USER:$APP_USER" "$ENV_FILE"
    ok ".env written ($ENV_FILE)"
else
    ok ".env already exists — leaving it alone"
fi

# ── 7. Django migrate + collectstatic ───────────────────────────────────────
say "Running migrations + collectstatic"
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && '$APP_DIR/.venv/bin/python' manage.py migrate --noinput"
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && '$APP_DIR/.venv/bin/python' manage.py collectstatic --noinput" >/dev/null
ok "Migrations applied + static collected"

# ── 8. systemd services ─────────────────────────────────────────────────────
say "Installing systemd services"
cat > /etc/systemd/system/bunurbek-web.service <<EOF
[Unit]
Description=Bu Nurbek — gunicorn web
After=network.target postgresql.service

[Service]
Type=notify
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/gunicorn config.wsgi \\
    --workers 2 --threads 4 --timeout 60 \\
    --bind 127.0.0.1:$WEB_PORT --log-file -
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/bunurbek-bot.service <<EOF
[Unit]
Description=Bu Nurbek — Telegram auth bot worker
After=network.target bunurbek-web.service

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/python manage.py run_telegram_bot
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now bunurbek-web.service
systemctl enable --now bunurbek-bot.service
ok "Web + bot services running"

# ── 9. Nginx server block ───────────────────────────────────────────────────
say "Adding nginx server block (won't touch your IELTS config)"
NGINX_CONF="/etc/nginx/sites-available/bunurbek"

if [[ "$DOMAIN" != "skip" && -n "$DOMAIN" ]]; then
    cat > "$NGINX_CONF" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    client_max_body_size 200M;  # for video / homework PDF uploads

    location /static/ {
        alias $APP_DIR/staticfiles/;
        expires 30d;
        access_log off;
    }
    location /media/ {
        alias $APP_DIR/media/;
        expires 30d;
    }
    location / {
        proxy_pass http://127.0.0.1:$WEB_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
EOF
else
    # IP+port mode — listen directly on port 8001 with no server_name
    cat > "$NGINX_CONF" <<EOF
server {
    listen $WEB_PORT;
    listen [::]:$WEB_PORT;

    client_max_body_size 200M;

    location /static/ {
        alias $APP_DIR/staticfiles/;
        expires 30d;
        access_log off;
    }
    location /media/ {
        alias $APP_DIR/media/;
        expires 30d;
    }
    location / {
        proxy_pass http://127.0.0.1:$((WEB_PORT + 1));
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
    # Re-point gunicorn to port +1 since nginx now owns 8001
    sed -i "s|--bind 127.0.0.1:$WEB_PORT|--bind 127.0.0.1:$((WEB_PORT + 1))|" /etc/systemd/system/bunurbek-web.service
    systemctl daemon-reload && systemctl restart bunurbek-web
fi

ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/bunurbek
nginx -t && systemctl reload nginx
ok "Nginx reloaded — Bu Nurbek server block live"

# ── 10. Firewall (only opens needed ports) ──────────────────────────────────
if command -v ufw >/dev/null; then
    say "Opening firewall ports (idempotent)"
    ufw allow 22/tcp comment 'SSH' >/dev/null 2>&1 || true
    ufw allow 80/tcp comment 'HTTP' >/dev/null 2>&1 || true
    ufw allow 443/tcp comment 'HTTPS' >/dev/null 2>&1 || true
    [[ "$DOMAIN" == "skip" ]] && ufw allow $WEB_PORT/tcp comment 'Bu Nurbek' >/dev/null 2>&1 || true
    ok "Firewall rules updated"
fi

# ── 11. Final summary ───────────────────────────────────────────────────────
say "════════ DONE ═══════════════════════════════"
echo ""
if [[ "$DOMAIN" != "skip" && -n "$DOMAIN" ]]; then
    echo "  🌐  http://$DOMAIN  (run 'certbot --nginx -d $DOMAIN' for HTTPS)"
else
    echo "  🌐  http://$DROPLET_IP:$WEB_PORT"
fi
echo ""
echo "  Logs:"
echo "    journalctl -u bunurbek-web -f"
echo "    journalctl -u bunurbek-bot -f"
echo ""
echo "  Create admin user:"
echo "    cd $APP_DIR && sudo -u $APP_USER .venv/bin/python manage.py createsuperuser"
echo ""
echo "  Re-deploy after git push:"
echo "    cd $APP_DIR && sudo -u $APP_USER git pull && sudo -u $APP_USER .venv/bin/pip install -r requirements.txt && \\"
echo "      sudo -u $APP_USER .venv/bin/python manage.py migrate --noinput && \\"
echo "      sudo -u $APP_USER .venv/bin/python manage.py collectstatic --noinput && \\"
echo "      systemctl restart bunurbek-web bunurbek-bot"
echo ""
ok "Installation complete"
