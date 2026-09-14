# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: S603, S607
"""Deterministic, offline verification of the deployment assets (Integration Task 4)."""

import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_gunicorn_has_exact_worker_and_port_contract():
    conf = runpy.run_path(str(ROOT / "gunicorn.conf.py"))
    assert conf["workers"] == 2
    # Secure default bind: loopback only unless MICROPAD_ADMIN_SECRET is set.
    assert conf["bind"] == "127.0.0.1:8080"
    assert conf["accesslog"] == "-"
    assert conf["errorlog"] == "-"


def test_systemd_unit_is_unprivileged_and_uses_deployment_root():
    unit = (ROOT / "deploy/micropad.service").read_text(encoding="utf-8")
    required = [
        "User=micropad", "Group=micropad", "WorkingDirectory=/opt/micropad/app",
        "ExecStart=/opt/micropad/app/.venv/bin/gunicorn --config /opt/micropad/app/gunicorn.conf.py micropad.wsgi:app",
        "NoNewPrivileges=true", "PrivateTmp=true", "ProtectSystem=strict",
        "Environment=MICROPAD_CONFIG_PATH=/var/lib/micropad/config.json",
        "ReadWritePaths=/var/lib/micropad",
    ]
    assert all(value in unit for value in required)


def test_installer_has_valid_shell_syntax_and_requires_explicit_enable():
    subprocess.run(["bash", "-n", str(ROOT / "scripts/setup.sh")], check=True)
    script = (ROOT / "scripts/setup.sh").read_text(encoding="utf-8")
    assert 'if [[ "${1:-}" == "--enable-service" ]]' in script
    assert "systemctl enable --now micropad.service" in script
