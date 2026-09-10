#!/usr/bin/env bash
# MicroPad Config Generator - Ubuntu LXC install script
# Run as root on the container:
#   bash /opt/micropad/setup.sh
# Expects the app/ directory next to this script (i.e. /opt/micropad/app).
set -euo pipefail

SERVICE_NAME="micropad"
PORT="${MICROPAD_PORT:-8080}"

# Locate the app dir: this script sits in .../deploy/, app is its sibling.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The app dir is a sibling of this script (i.e. .../deploy -> .../app).
if [ -d "$SCRIPT_DIR/../app" ]; then
  APP_SRC="$(cd "$SCRIPT_DIR/../app" && pwd)"
  APP_DIR="$(dirname "$APP_SRC")"
elif [ -d "$SCRIPT_DIR/app" ]; then
  APP_SRC="$SCRIPT_DIR/app"
  APP_DIR="$SCRIPT_DIR"
else
  echo "!! Could not find app/ directory next to this script."
  echo "   Place the whole unpacked folder (containing app/ and deploy/)"
  echo "   at /opt/micropad and run: bash /opt/micropad/deploy/setup.sh"
  exit 1
fi

echo "==> Updating apt and installing python3 + venv"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip python3-yaml

echo "==> Venv"
if [ ! -d "$APP_DIR/.venv" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$APP_DIR/.venv/bin/pip" install -r "$APP_SRC/requirements.txt"

echo "==> systemd service (port $PORT)"
cat > /etc/systemd/system/$SERVICE_NAME.service <<EOF
[Unit]
Description=MicroPad Config Generator
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_SRC
Environment=MICROPAD_PORT=$PORT
ExecStart=$APP_DIR/.venv/bin/gunicorn --workers 2 --bind 0.0.0.0:$PORT --chdir $APP_SRC wsgi:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl restart $SERVICE_NAME

echo "==> Done."
systemctl status $SERVICE_NAME --no-pager | head -8
echo ""
echo "Open in browser:  http://<container-ip>:$PORT"