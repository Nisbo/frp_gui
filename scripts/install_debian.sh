#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="/etc/frp-gui.env"
SERVICE_FILE="/etc/systemd/system/frp-gui.service"
NGINX_SITE="/etc/nginx/sites-available/frp-gui.conf"
NGINX_LINK="/etc/nginx/sites-enabled/frp-gui.conf"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run this installer as root."
  exit 1
fi

echo "Installing FRP Gui from: ${APP_DIR}"

apt update
apt install -y git python3 python3-flask gunicorn nginx

FRP_CONFIG_PATH="${FRP_CONFIG_PATH:-/opt/frp/frpc.ini}"
FRPC_BINARY="${FRPC_BINARY:-/opt/frp/frpc}"
FRPC_SERVICE="${FRPC_SERVICE:-frpc}"
FRP_GUI_HOST="${FRP_GUI_HOST:-127.0.0.1}"
FRP_GUI_PORT="${FRP_GUI_PORT:-8844}"
FRP_GUI_PUBLIC_PORT="${FRP_GUI_PUBLIC_PORT:-8844}"

if [[ -x "${APP_DIR}/scripts/detect_frp.py" ]]; then
  DETECTED_CONFIG="$(python3 "${APP_DIR}/scripts/detect_frp.py" | python3 -c 'import json,sys; data=json.load(sys.stdin); rec=data.get("recommended") or {}; print(rec.get("config_path") or "")' || true)"
  DETECTED_BINARY="$(python3 "${APP_DIR}/scripts/detect_frp.py" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("frpc_binary") or "")' || true)"
  DETECTED_SERVICE="$(python3 "${APP_DIR}/scripts/detect_frp.py" | python3 -c 'import json,sys; data=json.load(sys.stdin); rec=data.get("recommended") or {}; print((rec.get("service") or "").removesuffix(".service"))' || true)"
  [[ -n "${DETECTED_CONFIG}" ]] && FRP_CONFIG_PATH="${DETECTED_CONFIG}"
  [[ -n "${DETECTED_BINARY}" ]] && FRPC_BINARY="${DETECTED_BINARY}"
  [[ -n "${DETECTED_SERVICE}" ]] && FRPC_SERVICE="${DETECTED_SERVICE}"
fi

if [[ -f "${ENV_FILE}" ]]; then
  EXISTING_PASSWORD="$(grep -E '^FRP_GUI_PASSWORD=' "${ENV_FILE}" | cut -d= -f2- || true)"
  EXISTING_SECRET="$(grep -E '^FRP_GUI_SECRET=' "${ENV_FILE}" | cut -d= -f2- || true)"
else
  EXISTING_PASSWORD=""
  EXISTING_SECRET=""
fi

FRP_GUI_PASSWORD="${FRP_GUI_PASSWORD:-${EXISTING_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')}}"
FRP_GUI_SECRET="${FRP_GUI_SECRET:-${EXISTING_SECRET:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}}"

install -d -m 0755 "$(dirname "${ENV_FILE}")"
cat > "${ENV_FILE}" <<EOF
FRP_CONFIG_PATH=${FRP_CONFIG_PATH}
FRPC_BINARY=${FRPC_BINARY}
FRPC_SERVICE=${FRPC_SERVICE}
FRP_GUI_ALLOW_SYSTEMCTL=1
FRP_GUI_PASSWORD=${FRP_GUI_PASSWORD}
FRP_GUI_SECRET=${FRP_GUI_SECRET}
FRP_GUI_HOST=${FRP_GUI_HOST}
FRP_GUI_PORT=${FRP_GUI_PORT}
FRP_GUI_PUBLIC_PORT=${FRP_GUI_PUBLIC_PORT}
EOF
chmod 600 "${ENV_FILE}"

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=FRP Gui
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=/usr/bin/gunicorn -w 2 -b \${FRP_GUI_HOST}:\${FRP_GUI_PORT} 'frp_gui:create_app()'
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF

cat > "${NGINX_SITE}" <<EOF
server {
    listen ${FRP_GUI_PUBLIC_PORT};
    server_name _;

    location / {
        proxy_pass http://${FRP_GUI_HOST}:${FRP_GUI_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

ln -sf "${NGINX_SITE}" "${NGINX_LINK}"

systemctl daemon-reload
systemctl enable --now frp-gui
nginx -t
systemctl reload nginx

echo
echo "FRP Gui installed."
echo "Open: http://YOUR-SERVER-IP:${FRP_GUI_PUBLIC_PORT}"
echo "Password: ${FRP_GUI_PASSWORD}"
echo
echo "Config file: ${FRP_CONFIG_PATH}"
echo "frpc binary: ${FRPC_BINARY}"
echo "frpc service: ${FRPC_SERVICE}"
echo
echo "Settings are stored in: ${ENV_FILE}"
