# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: S603, S607
"""Deterministic safety and consistency tests for the deployment assets (Task 14)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

from micropad.constants import AI_NOTICE


def test_gunicorn_and_systemd_contract() -> None:
    gunicorn = Path("gunicorn.conf.py").read_text(encoding="utf-8")
    service = Path("deploy/micropad.service").read_text(encoding="utf-8")
    assert 'bind = "0.0.0.0:8080"' in gunicorn
    assert "workers = 2" in gunicorn
    assert "User=micropad" in service
    assert "WorkingDirectory=/opt/micropad/app" in service
    assert "micropad.wsgi:app" in service
    assert "Restart=on-failure" in service


def test_gunicorn_config_loads_as_python_safely() -> None:
    namespace: dict[str, object] = {}
    exec(Path("gunicorn.conf.py").read_text(encoding="utf-8"), namespace)  # noqa: S102
    assert namespace["bind"] == "0.0.0.0:8080"
    assert namespace["workers"] == 2
    assert namespace["timeout"] == 30
    assert namespace["accesslog"] == "-"
    assert namespace["errorlog"] == "-"
    assert namespace["capture_output"] is True


def test_setup_is_explicit_root_action_and_never_overwrites_config() -> None:
    setup = Path("scripts/setup.sh").read_text(encoding="utf-8")
    assert '[[ "${EUID}" -eq 0 ]]' in setup
    assert "python3 -m venv" in setup
    assert 'if [[ ! -e "$data_root/config.json" ]]' in setup
    assert 'install -o micropad -g micropad -m 0600 "$install_root/config.example.json" "$data_root/config.json"' in setup
    assert 'if [[ "${1:-}" == "--enable-service" ]]' in setup
    assert "systemctl enable --now micropad.service" in setup


def test_setup_script_is_executable_and_shell_valid() -> None:
    setup = Path("scripts/setup.sh")
    assert setup.stat().st_mode & 0o111
    result = subprocess.run(["bash", "-n", str(setup)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_example_automation_is_parseable_and_carries_notice() -> None:
    text = Path("homeassistant/automation.example.yaml").read_text(encoding="utf-8")
    assert text.startswith(f"# {AI_NOTICE}\n")
    assert yaml.safe_load(text)["id"] == "micropad_controller"


def test_example_automation_is_byte_identical_to_fresh_generate_bundle() -> None:
    from micropad.generator import generate_bundle
    from micropad.models import parse_config

    root = Path(__file__).resolve().parents[1]
    config = parse_config(json.loads((root / "config.example.json").read_text(encoding="utf-8")))
    checked_in = (root / "homeassistant" / "automation.example.yaml").read_text(encoding="utf-8")
    assert checked_in == generate_bundle(config).yaml_text


def test_wsgi_entry_point_resolves_and_serves_healthz() -> None:
    tmpdir = tempfile.mkdtemp(prefix="micropad-wsgi-")
    tmp_example = Path(tmpdir) / "config.example.json"
    tmp_example.write_text(
        Path("config.example.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.environ["MICROPAD_CONFIG"] = os.path.join(tmpdir, "config.json")
    os.environ["MICROPAD_CONFIG_EXAMPLE"] = os.path.join(tmpdir, "config.example.json")

    from micropad import wsgi

    response = wsgi.app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "service": "micropad-configurator"}
