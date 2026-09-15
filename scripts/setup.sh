#!/usr/bin/env bash
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
set -Eeuo pipefail

# The install is root-gated: nothing is written or enabled before this check.
[[ "${EUID}" -eq 0 ]] || { printf '%s\n' 'Run this setup script as root.' >&2; exit 1; }

if [[ "${1:-}" == "--enable-service" ]]; then
    enable_service=1
elif [[ -n "${1:-}" ]]; then
    printf 'Unknown argument (use the exact --enable-service flag): %s\n' "$1" >&2
    exit 2
else
    enable_service=0
fi

install_root=/opt/micropad/app
data_root=/var/lib/micropad

# Service account without a login shell.
id micropad >/dev/null 2>&1 || useradd --system --no-create-home --home-dir "$install_root" --shell /usr/sbin/nologin micropad

# root-owned application tree; config lives under the user-owned data root.
install -d -o root -g root -m 0755 "$install_root"
install -d -o micropad -g micropad -m 0750 "$data_root"

cp -a pyproject.toml requirements.txt config.example.json src gunicorn.conf.py deploy "$install_root/"

python3 -m venv "$install_root/.venv"
"$install_root/.venv/bin/python" -m pip install --requirement "$install_root/requirements.txt"
"$install_root/.venv/bin/python" -m pip install --no-deps "$install_root"

# Seed the configuration only when absent so re-runs never overwrite admin edits.
if [[ ! -e "$data_root/config.json" ]]; then
    install -o micropad -g micropad -m 0600 "$install_root/config.example.json" "$data_root/config.json"
fi

chown -R root:root "$install_root"
chown -R micropad:micropad "$data_root"

install -o root -g root -m 0644 "$install_root/deploy/micropad.service" /etc/systemd/system/micropad.service
systemctl daemon-reload
if [[ "${enable_service}" -eq 1 ]]; then
    systemctl enable --now micropad.service
fi
