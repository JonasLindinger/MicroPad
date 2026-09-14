# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Workflow validation (P1.17, P1.20, P2.1): the actionlint-equivalent gate.

Without installing actionlint, statically verify the GitHub workflows:
* every workflow parses as YAML and references only pinned commit SHAs (P2.1);
* firmware matrix artifact names use a safe fixed id, never the FQBN which
  contains ':' and is invalid in GitHub artifact names (P1.17);
* the firmware matrix pins exactly the three expected FQBNs and the release job
  downloads all of them; CI runs `make ci` with all gates (P1.20).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _uses_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if "uses:" in line]


def test_workflows_parse_as_yaml() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)


def test_all_third_party_actions_are_pinned_to_commit_shas() -> None:
    sha = re.compile(r"^[0-9a-f]{40}$")
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        for line in _uses_lines(text):
            match = re.search(r"@([A-Za-z0-9_.-]+)", line)
            assert match, f"uses line without version: {line}"
            ref = match.group(1)
            assert sha.match(ref), (
                f"{path.name}: mutable action tag {ref!r} must be pinned to a "
                f"commit SHA (P2.1)"
            )


def test_ci_workflow_runs_the_full_gate_via_make_ci() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "make ci" in text


def test_firmware_artifact_names_use_safe_matrix_ids() -> None:
    text = (WORKFLOWS / "firmware.yml").read_text(encoding="utf-8")
    ids = re.findall(r"id:\s*([a-z0-9][a-z0-9-]*)\s*$", text, re.MULTILINE)
    assert ids == ["hwcdc-psram", "tinyusb-psram", "hwcdc-no-psram"]
    # Artifact names must use matrix.id and never the raw FQBN (P1.17).
    assert "micropad-${{ matrix.id }}" in text
    assert "micropad-${{ matrix.fqbn }}" not in text


def test_firmware_matrix_pins_exactly_the_three_fqbns() -> None:
    data = yaml.safe_load((WORKFLOWS / "firmware.yml").read_text(encoding="utf-8"))
    fqbns = [entry["fqbn"] for entry in data["jobs"]["build"]["strategy"]["matrix"]["include"]]
    assert fqbns == [
        "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,PSRAM=opi",
        "esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=cdc,PSRAM=opi",
        "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=default,PSRAM=disabled",
    ]


def test_firmware_release_uses_download_without_fqbn_artifact_names() -> None:
    text = (WORKFLOWS / "firmware.yml").read_text(encoding="utf-8")
    assert "actions/download-artifact" in text
    assert "micropad-${{ matrix.id }}" in text