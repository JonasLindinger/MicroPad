# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""The host simulator, the panel-preview API, the config analysis and its CLI.

``tools/micropad_sim.cpp`` is what makes the configurator's preview honest: it runs
the shipped core, so these tests pin the tool, the wrapper and the two routes —
plus the recorded browser fixture, so a firmware layout change cannot leave the
website drawing yesterday's panel.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from micropad import simulator
from micropad.lint import analyze

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def simulator_binary() -> Path:
    """Build the simulator once per session (plain g++, no toolchain needed)."""
    script = REPO_ROOT / "scripts" / "build-simulator.sh"
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        pytest.skip(f"cannot build the simulator: {result.stderr.strip()}")
    binary = REPO_ROOT / "build" / "host" / "micropad_sim"
    assert binary.is_file()
    return binary


@pytest.fixture(autouse=True)
def _require_simulator(simulator_binary: Path) -> None:
    """Every test here needs the built binary (the fixture skips if g++ is absent)."""


def _page(**overrides: object) -> dict[str, object]:
    page: dict[str, object] = {
        "page_id": "home",
        "title": "Home",
        "items": [
            {"name": "Desk light", "type": "light", "entity": "light.desk", "state": "on", "value": 0},
            {"name": "Kitchen", "type": "sensor", "entity": "sensor.kitchen", "unit": "C", "value": 21.5},
        ],
    }
    page.update(overrides)
    return page


def test_tool_reports_the_firmware_build_it_compiled() -> None:
    header = (REPO_ROOT / "firmware" / "micropad_core.h").read_text(encoding="utf-8")
    expected = re.search(r'FIRMWARE_VERSION = "([^"]+)"', header)
    assert expected is not None
    result = simulator.simulate(simulator.page_directives(_page()))
    assert result["firmware"] == expected.group(1)


def test_simulate_renders_the_core_model_for_a_page() -> None:
    result = simulator.simulate(
        simulator.page_directives(_page(), selected=1, network=4, usb_host=True)
    )
    assert result["ok"] is True
    assert result["mode"] == "normal"
    assert result["title"] == "Home"
    assert result["total_items"] == 2
    assert result["row_count"] == 2
    # The selected row carries the cursor: the firmware draws it as a triangle in
    # the left gutter, so a selected-but-invisible row is a layout regression.
    assert result["rows"][1]["selected"] is True
    assert result["rows"][0]["selected"] is False
    assert sum(1 for prim in result["prims"] if prim["kind"] == "filltriangle") == 1
    texts = [prim["text"] for prim in result["prims"] if prim["kind"] == "text"]
    assert "Home" in texts
    assert any("Desk light" in text for text in texts)


def test_simulate_reports_what_the_pad_would_clip_and_reject() -> None:
    long_name = "N" * 60
    result = simulator.simulate(
        simulator.page_directives(_page(items=[
            {"name": long_name, "type": "light", "entity": "light.desk", "state": "S" * 60},
            {"name": "Broken", "type": "light", "entity": "light." + "y" * 100},
        ]))
    )
    codes = {(finding["code"], finding["severity"]) for finding in result["findings"]}
    assert ("field_clipped", "warning") in codes
    assert ("identifier_too_long", "error") in codes
    assert result["would_reject"] is True
    # Clipped display text still renders — a long value must not blank the panel.
    clipped = [prim["text"] for prim in result["prims"] if prim["kind"] == "text"]
    assert any(text.startswith("NNNN") for text in clipped)


def test_simulate_renders_the_portal_view_without_a_page() -> None:
    result = simulator.simulate(
        simulator.page_directives(
            # Portal previews use the documented placeholder value; the device
            # generates the real one itself and never hands it to the browser.
            {}, portal={"ssid": "MicroPad-Setup", "password": "replace-me", "address": "192.168.4.1"}
        )
    )
    assert result["mode"] == "portal"
    texts = [prim["text"] for prim in result["prims"] if prim["kind"] == "text"]
    assert any("MicroPad-Setup" in text for text in texts)
    assert any("192.168.4.1" in text for text in texts)


def test_simulate_resolves_a_key_press_through_the_core() -> None:
    result = simulator.simulate(
        simulator.page_directives(
            _page(),
            selected=0,
            keys=[{"key_id": "r0c3", "action": "enter"}, {"key_id": "enc_up", "action": "scroll_up"}],
            press="r0c3",
        )
    )
    assert result["press"]["key"] == "r0c3"
    assert result["press"]["binding_action"] == "enter"
    # The selected item is a light, so Enter on it toggles (core descriptor table).
    assert result["press"]["item_action"] == "toggle"


def test_malformed_input_is_rejected_with_a_reason() -> None:
    with pytest.raises(simulator.SimulatorError):
        simulator.simulate("nonsense 1\n")
    with pytest.raises(simulator.SimulatorError):
        simulator.simulate("page home Home\nitem Broken\t\t\t0\tnot-a-type\t\t\n")


def test_missing_binary_fails_loudly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(simulator.SIM_BIN_ENV, str(tmp_path / "absent"))
    assert simulator.simulator_available() is False
    with pytest.raises(simulator.SimulatorUnavailable):
        simulator.simulate("page home Home\n")


def test_recorded_browser_fixture_matches_the_current_core() -> None:
    """The browser suite draws a recorded prim model; it must not go stale."""
    fixture = json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "frontend_panel.json").read_text(encoding="utf-8")
    )
    config = json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "frontend_config.json").read_text(encoding="utf-8")
    )
    page = config["pages"][0]
    assert fixture["page_id"] == page["page_id"]
    fresh = simulator.simulate(
        simulator.page_directives(page, selected=0, network=4, usb_host=True, press="r0c3")
    )
    assert fresh["prims"] == fixture["response"]["prims"], (
        "firmware layout changed: regenerate tests/fixtures/frontend_panel.json"
    )
    assert fresh["rows"] == fixture["response"]["rows"]
    assert fresh["mode"] == fixture["response"]["mode"]
    # The recorded firmware version is what the browser preview shows, so it is part of
    # the recording: without this the preview would keep displaying the old version.
    assert fresh["firmware"] == fixture["response"]["firmware"]


def test_analysis_reports_budgets_and_field_findings() -> None:
    from tests.factories import valid_config

    config = json.loads(valid_config().model_dump_json())
    clean = analyze(config)
    assert clean.ok is True
    assert clean.findings == []
    assert clean.catalog_bytes > 0
    assert set(clean.page_bytes) == {"home", "bedroom", "office"}
    assert clean.limits["mqtt_buffer_bytes"] == 16384

    dirty = json.loads(json.dumps(config))
    dirty["pages"][0]["title"] = "T" * 40
    dirty["pages"][0]["items"][0]["entity"] = "light." + "y" * 100
    findings = analyze(dirty)
    assert findings.ok is False
    codes = {finding.code for finding in findings.findings}
    assert "field_clipped" in codes
    assert "identifier_too_long" in codes


def test_simulate_route_returns_the_core_model(client) -> None:
    response = client.post(
        "/api/simulate",
        json={"page": _page(), "state": {"selected": 1}, "network": 4, "usb_host": True},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["title"] == "Home"
    assert body["firmware"]
    assert body["rows"][1]["selected"] is True
    assert all(prim["kind"] for prim in body["prims"])


def test_simulate_route_reports_missing_binary(
    client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(simulator.SIM_BIN_ENV, str(tmp_path / "absent"))
    response = client.post("/api/simulate", json={"page": _page()})
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "simulator_unavailable"


def test_lint_route_reports_budgets_caps_and_findings(client) -> None:
    from tests.factories import valid_config

    config = json.loads(valid_config().model_dump_json())
    response = client.post("/api/lint", json=config)
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["limits"]["page_bytes"] == 8192
    assert body["caps"]["field_caps"]["entity"] == 96
    assert "home" in body["page_bytes"]

    config["pages"][0]["items"][0]["name"] = "N" * 40
    body = client.post("/api/lint", json=config).get_json()
    assert body["ok"] is True  # clipping is a warning, not a publish blocker
    assert [finding["code"] for finding in body["findings"]] == ["field_clipped"]
    assert body["findings"][0]["where"] == "pages[0].items[0].name"


def _write_config(tmp_path: Path, mutate=None) -> Path:
    from tests.factories import valid_config

    config = json.loads(valid_config().model_dump_json())
    if mutate is not None:
        mutate(config)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_lint_cli_reports_a_publishable_config(tmp_path: Path) -> None:
    script = REPO_ROOT / "scripts" / "lint_config.py"
    result = subprocess.run(
        [sys.executable, str(script), str(_write_config(tmp_path))],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "publishable" in result.stdout
    assert "findings: none" in result.stdout
    assert "page home:" in result.stdout


def test_lint_cli_fails_on_a_config_the_pad_would_refuse(tmp_path: Path) -> None:
    def mutate(config: dict) -> None:
        config["pages"][0]["title"] = "T" * 40
        config["pages"][0]["items"][0]["entity"] = "light." + "y" * 100

    path = _write_config(tmp_path, mutate)
    script = REPO_ROOT / "scripts" / "lint_config.py"
    result = subprocess.run(
        [sys.executable, str(script), str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1, result.stdout
    assert "NOT PUBLISHABLE" in result.stdout
    assert "identifier_too_long" in result.stdout
    assert "field_clipped" in result.stdout

    # --strict also fails on the clipping warning alone.
    def warn_only(config: dict) -> None:
        config["pages"][0]["title"] = "T" * 40

    strict_path = _write_config(tmp_path, warn_only)
    lenient = subprocess.run(
        [sys.executable, str(script), str(strict_path)], capture_output=True, text=True, check=False
    )
    strict = subprocess.run(
        [sys.executable, str(script), str(strict_path), "--strict"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert lenient.returncode == 0
    assert strict.returncode == 1


def test_lint_cli_json_mode_and_unreadable_file(tmp_path: Path) -> None:
    script = REPO_ROOT / "scripts" / "lint_config.py"
    result = subprocess.run(
        [sys.executable, str(script), str(_write_config(tmp_path)), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert "home" in body["page_bytes"]

    missing = subprocess.run(
        [sys.executable, str(script), str(tmp_path / "absent.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 2
    assert "cannot read" in missing.stderr
