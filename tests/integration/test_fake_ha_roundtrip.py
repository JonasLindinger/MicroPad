# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Fake-HA round trip and hash-locked live-apply gate integration tests.

These tests prove the full configurator → Home Assistant deployment round trip over
real HTTP against the thread-backed :class:`FakeHomeAssistant`, and verify the
release scripts fail closed: they refuse to apply or verify unless an operator
explicitly approves a matching preview digest and configures the endpoint. No live
Home Assistant, MQTT broker, or MicroPad hardware is ever contacted.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from micropad.generator import generate_bundle
from micropad.ha_client import HomeAssistantClient
from micropad.ha_deploy import (
    PREVIEW_FILES,
    HADeploymentError,
    HomeAssistantDeployer,
    preview_sha256,
)
from micropad.models import parse_config
from tests.factories import valid_config
from tests.fakes.fake_ha import FakeHomeAssistant

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts"


def _run_script(script: str, *args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / script), *args],
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _deploy(config) -> HomeAssistantDeployer:
    return HomeAssistantDeployer(HomeAssistantClient(config.settings))


def _write_config(tmp_path: Path, ha_url: str | None = None) -> Path:
    config = valid_config()
    if ha_url:
        config.settings.ha_url = ha_url
    config.settings.ha_token = ""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(config.model_dump(by_alias=True), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def _render(tmp_path: Path, config_path: Path, name: str = "preview") -> Path:
    preview = tmp_path / name
    result = _run_script(
        "render_live_ha_preview.py",
        "--config",
        str(config_path),
        "--output",
        str(preview),
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return preview


def _failing_config() :
    config = valid_config()
    config.settings.ha_token = "test-token"  # noqa: S105 — synthetic placeholder credential
    return config


# --- Step 1 / 6: full deterministic fake-HA round trip -------------------------


def test_create_reload_readback_and_retained_publications() -> None:
    with FakeHomeAssistant() as ha:
        config = _failing_config()
        config.settings.ha_url = ha.url
        bundle = generate_bundle(config)
        result = _deploy(config).deploy(config)
        assert result.automation_id == "micropad_controller"
        assert result.created is True
        assert result.published_topics == (
            "micropad/pages/all",
            "micropad/page/current",
            "micropad/keymap",
        )
        assert ha.reload_count == 1
        assert ha.read_automation("micropad_controller") == bundle.automation
        assert [(call["topic"], call["retain"]) for call in ha.mqtt_calls] == [
            ("micropad/pages/all", True),
            ("micropad/page/current", True),
            ("micropad/keymap", True),
        ]


def test_update_path_reports_not_created() -> None:
    with FakeHomeAssistant() as ha:
        ha.automation = {"id": "micropad_controller"}
        config = _failing_config()
        config.settings.ha_url = ha.url
        result = _deploy(config).deploy(config)
        assert result.created is False
        assert result.published_topics == (
            "micropad/pages/all",
            "micropad/page/current",
            "micropad/keymap",
        )
        assert ha.automation_writes == 1


def test_mismatched_read_back_raises_without_publishing() -> None:
    with FakeHomeAssistant() as ha:
        ha.read_back_transform = lambda automation: {**automation, "alias": "Tampered"}
        config = _failing_config()
        config.settings.ha_url = ha.url
        with pytest.raises(HADeploymentError, match="did not match"):
            _deploy(config).deploy(config)
        assert ha.mqtt_calls == []


def test_failed_publication_raises_without_success() -> None:
    with FakeHomeAssistant() as ha:
        ha.set_status("publish", 500)
        config = _failing_config()
        config.settings.ha_url = ha.url
        with pytest.raises(HADeploymentError):
            _deploy(config).deploy(config)


def test_server_authentication_failure_aborts_before_mqtt() -> None:
    with FakeHomeAssistant() as ha:
        ha.set_status("api_root", 401)
        config = _failing_config()
        config.settings.ha_url = ha.url
        with pytest.raises(HADeploymentError):
            _deploy(config).deploy(config)
        assert ha.mqtt_calls == []


def test_empty_token_refuses_before_any_request() -> None:
    with FakeHomeAssistant() as ha:
        config = _failing_config()
        config.settings.ha_url = ha.url
        config.settings.ha_token = ""
        with pytest.raises(HADeploymentError, match="token"):
            _deploy(config).deploy(config)
        assert ha.request_log == []


# --- Step 5: deterministic preview generation ----------------------------------


def test_render_preview_is_deterministic_and_self_consistent(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = parse_config(json.loads(config_path.read_text(encoding="utf-8")))
    first = _render(tmp_path, config_path, "first")
    second = _render(tmp_path, config_path, "second")
    assert (first / "release.sha256").read_text(encoding="utf-8").strip() == (
        second / "release.sha256"
    ).read_text(encoding="utf-8").strip()
    assert preview_sha256(first) == preview_sha256(second) == (
        first / "release.sha256"
    ).read_text(encoding="utf-8").strip()
    for filename in (*PREVIEW_FILES, "release.sha256"):
        assert (first / filename).is_file()
    assert json.loads((first / "automation.json").read_text(encoding="utf-8")) == generate_bundle(
        config, None
    ).automation
    publications = json.loads((first / "mqtt-publications.json").read_text(encoding="utf-8"))
    assert [entry["topic"] for entry in publications["publications"]] == [
        "micropad/pages/all",
        "micropad/page/current",
        "micropad/keymap",
    ]
    assert all(entry["retain"] is True for entry in publications["publications"])


def test_example_config_renders_preview_twice_into_identical_digest(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    example = _REPO / "config.example.json"
    result = _run_script(
        "render_live_ha_preview.py",
        "--config",
        str(example),
        "--output",
        str(first),
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    result = _run_script(
        "render_live_ha_preview.py",
        "--config",
        str(example),
        "--output",
        str(second),
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (first / "release.sha256").read_text(encoding="utf-8").strip() == (
        second / "release.sha256"
    ).read_text(encoding="utf-8").strip()


# --- Step 6: hash-locked live apply gate ---------------------------------------


def test_apply_with_wrong_approval_hash_makes_zero_network_requests(tmp_path: Path) -> None:
    with FakeHomeAssistant() as ha:
        config_path = _write_config(tmp_path, ha_url=ha.url)
        preview = _render(tmp_path, config_path)
        assert preview_sha256(preview) != "0" * 64
        result = _run_script(
            "apply_live_ha_preview.py",
            "--preview",
            str(preview),
            "--config",
            str(config_path),
            "--approved-sha256",
            "0" * 64,
            "--readback-output",
            str(tmp_path / "readback.json"),
            cwd=tmp_path,
            env={"MICROPAD_HA_TOKEN": "test-token"},
        )
        assert result.returncode != 0
        assert "does not match" in (result.stdout + result.stderr)
        assert ha.request_log == []
        assert not (tmp_path / "readback.json").exists()


def test_apply_refuses_malformed_approval_hash_before_network(tmp_path: Path) -> None:
    with FakeHomeAssistant() as ha:
        config_path = _write_config(tmp_path, ha_url=ha.url)
        preview = _render(tmp_path, config_path)
        result = _run_script(
            "apply_live_ha_preview.py",
            "--preview",
            str(preview),
            "--config",
            str(config_path),
            "--approved-sha256",
            "not-a-hash",
            "--readback-output",
            str(tmp_path / "readback.json"),
            cwd=tmp_path,
            env={"MICROPAD_HA_TOKEN": "test-token"},
        )
        assert result.returncode != 0
        assert ha.request_log == []


def test_apply_refuses_missing_token_before_network(tmp_path: Path) -> None:
    with FakeHomeAssistant() as ha:
        config_path = _write_config(tmp_path, ha_url=ha.url)
        preview = _render(tmp_path, config_path)
        digest = preview_sha256(preview)
        result = _run_script(
            "apply_live_ha_preview.py",
            "--preview",
            str(preview),
            "--config",
            str(config_path),
            "--approved-sha256",
            digest,
            "--readback-output",
            str(tmp_path / "readback.json"),
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "MICROPAD_HA_TOKEN" in result.stdout + result.stderr
        assert ha.request_log == []


def test_apply_refuses_config_drifted_from_approved_preview(tmp_path: Path) -> None:
    with FakeHomeAssistant() as ha:
        config_path = _write_config(tmp_path, ha_url=ha.url)
        preview = _render(tmp_path, config_path)
        digest = preview_sha256(preview)
        drifted = parse_config(json.loads(config_path.read_text(encoding="utf-8")))
        drifted.pages[0].title = "Drifted title"
        drifted_path = tmp_path / "drifted.json"
        drifted_path.write_text(
            json.dumps(drifted.model_dump(by_alias=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result = _run_script(
            "apply_live_ha_preview.py",
            "--preview",
            str(preview),
            "--config",
            str(drifted_path),
            "--approved-sha256",
            digest,
            "--readback-output",
            str(tmp_path / "readback.json"),
            cwd=tmp_path,
            env={"MICROPAD_HA_TOKEN": "test-token"},
        )
        assert result.returncode != 0
        assert "no longer matches" in result.stdout + result.stderr
        assert ha.request_log == []


def test_apply_with_matching_approved_hash_deploys_to_fake_ha(tmp_path: Path) -> None:
    with FakeHomeAssistant() as ha:
        config_path = _write_config(tmp_path, ha_url=ha.url)
        preview = _render(tmp_path, config_path)
        digest = preview_sha256(preview)
        readback_path = tmp_path / "readback.json"
        result = _run_script(
            "apply_live_ha_preview.py",
            "--preview",
            str(preview),
            "--config",
            str(config_path),
            "--approved-sha256",
            digest,
            "--readback-output",
            str(readback_path),
            cwd=tmp_path,
            env={"MICROPAD_HA_TOKEN": "test-token"},
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert ha.automation is not None
        assert ha.reload_count == 1
        assert readback_path.is_file()
        document = json.loads(readback_path.read_text(encoding="utf-8"))
        assert document["automation_id"] == "micropad_controller"
        assert document["deploy_created"] is True
        assert document["live_readback_matches_approved"] is True
        assert document["published_topics"] == [
            "micropad/pages/all",
            "micropad/page/current",
            "micropad/keymap",
        ]
        # Evidence must stay secret-free even against a live apply.
        assert "test-token" not in readback_path.read_text(encoding="utf-8")


# --- Step 7: live MQTT verification fails closed without a configured endpoint ---


def test_verify_live_mqtt_fails_closed_without_mqtt_endpoint(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)  # settings carry no mqtt_host
    output = tmp_path / "mqtt-readback.json"
    result = _run_script(
        "verify_live_mqtt.py",
        "--config",
        str(config_path),
        "--publications",
        str(tmp_path / "publications.json"),
        "--output",
        str(output),
        "--timeout",
        "2",
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "no MQTT endpoint configured" in result.stdout + result.stderr
    assert not output.exists()


def test_verify_live_mqtt_fails_closed_without_preview_publications(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "settings": {
                    "mqtt_host": "127.0.0.1",
                    "mqtt_port": 1883,
                    "mqtt_user": "",
                    "mqtt_password": "",
                }
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "mqtt-readback.json"
    result = _run_script(
        "verify_live_mqtt.py",
        "--config",
        str(config_path),
        "--publications",
        str(tmp_path / "missing-publications.json"),
        "--output",
        str(output),
        "--timeout",
        "2",
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "no preview publications" in result.stdout + result.stderr