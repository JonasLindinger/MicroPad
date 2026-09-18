# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Golden-file tests for everything the generator publishes (A7).

Every artifact that leaves this project — the Home Assistant automation YAML, the
retained catalog/current-page/keymap payloads — is byte-sensitive: the pad parses
them and Home Assistant executes them. A generator refactor can therefore change
what a deployment publishes without failing a single unit test, because the unit
tests assert *properties*, not the exact bytes.

These tests freeze the bytes for one realistic configuration
(``tests/fixtures/golden_config.json``). The artifacts are stored as real files
under ``tests/fixtures/golden/`` — not inside one JSON blob — so that an intended
change produces a readable, line-oriented diff to review. When a change is
intended, regenerate deliberately:

    MICROPAD_UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/test_golden_output.py

The config fixture is validated on every run as well, so it cannot rot into an
invalid example.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from micropad.generator import generate_bundle
from micropad.models import parse_config

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "tests" / "fixtures" / "golden_config.json"
GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "golden"
UPDATE_ENV = "MICROPAD_UPDATE_GOLDEN"

#: artifact name -> golden file name under tests/fixtures/golden/
GOLDEN_FILES = {
    "automation_yaml": "automation.yaml",
    "catalog_payload": "catalog.json",
    "home_payload": "page_home.json",
    "home_keymap_payload": "keymap_home.json",
    "player_queue_payload": "player_queue.json",
}


def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _current_bundle() -> dict[str, str]:
    config = parse_config(_config())
    bundle = generate_bundle(config)
    return {
        "automation_yaml": bundle.yaml_text,
        "catalog_payload": bundle.catalog_payload,
        "home_payload": bundle.home_payload,
        "home_keymap_payload": bundle.home_keymap_payload,
        "player_queue_payload": bundle.player_queue_payload,
    }


@pytest.fixture(scope="module")
def golden() -> dict[str, str]:
    current = _current_bundle()
    if os.environ.get(UPDATE_ENV):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        for name, filename in GOLDEN_FILES.items():
            text = current[name]
            if name != "automation_yaml":
                # Pretty-print the payloads so a diff shows the changed field, not
                # one rewritten line.
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False, sort_keys=False)
            if not text.endswith("\n"):
                text += "\n"
            (GOLDEN_DIR / filename).write_text(text, encoding="utf-8")
        pytest.skip(f"{UPDATE_ENV} set: rewrote {len(GOLDEN_FILES)} golden files")
    stored: dict[str, str] = {}
    for name, filename in GOLDEN_FILES.items():
        text = (GOLDEN_DIR / filename).read_text(encoding="utf-8")
        if name != "automation_yaml":
            # Compare structurally so the pretty-printed fixture still pins the
            # published bytes: the compact payload is what goes over MQTT.
            stored[name] = json.dumps(
                json.loads(text), ensure_ascii=False, separators=(",", ":")
            )
        else:
            # The YAML is stored verbatim (its trailing newline included): the file
            # that gets uploaded must match the file under test exactly.
            stored[name] = text
    return stored


def test_golden_config_fixture_is_valid() -> None:
    """The example the goldens are generated from must stay a valid config."""
    config = parse_config(_config())
    assert [page.page_id for page in config.pages] == ["home", "bedroom", "office"]
    assert "*" not in CONFIG_PATH.read_text(encoding="utf-8")


def test_generated_artifacts_match_the_golden_bytes(golden: dict[str, str]) -> None:
    current = _current_bundle()
    assert set(current) == set(golden)
    for name, value in current.items():
        assert value == golden[name], (
            f"{name} changed; review the diff and, if it is intended, regenerate with "
            f"{UPDATE_ENV}=1 (see tests/test_golden_output.py)"
        )


def test_generation_is_deterministic(golden: dict[str, str]) -> None:
    """Two runs of the same config must produce identical bytes, not just equal objects."""
    first = _current_bundle()
    second = _current_bundle()
    assert first == second
    assert first == golden


def test_golden_yaml_carries_the_notice_and_the_expected_shape(golden: dict[str, str]) -> None:
    yaml_text = golden["automation_yaml"]
    assert yaml_text.startswith("# AI-assisted development")
    assert "id: micropad_controller" in yaml_text
    assert "micropad/event" in yaml_text


def test_golden_payloads_stay_inside_the_contract_ceilings(golden: dict[str, str]) -> None:
    """A golden that drifted past a ceiling would freeze an unpublishable config."""
    from micropad.lint import analyze

    analysis = analyze(_config())
    assert analysis.ok is True
    assert analysis.catalog_bytes == len(golden["catalog_payload"].encode("utf-8"))
    assert analysis.limits["catalog_bytes"] > analysis.catalog_bytes
    assert analysis.limits["mqtt_buffer_bytes"] > analysis.catalog_bytes
