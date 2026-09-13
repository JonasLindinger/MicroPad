# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Orchestration-structure contract for the reproducible clean-room CI (Task 7).

Proves, without executing the build:
* ``scripts/ci.sh`` declares every required named stage in the mandated order.
* the approval builder's ``REQUIRED_CI_STAGES`` matches ``scripts/ci.sh``
  exactly (name set and order), so a stage added to CI can never silently drift
  out of the approval gate again (the audit found the two had diverged at ten
  vs. twelve stages).
* ``scripts/compile-firmware.sh`` pins the exact Arduino CLI core and library
  versions as literal strings, so the device build cannot drift.
"""
from pathlib import Path

from scripts.build_approval_bundle import REQUIRED_CI_STAGES

ROOT = Path(__file__).resolve().parents[2]


def _ci_stages() -> list[str]:
    """Stage names in the exact order ``scripts/ci.sh`` runs them."""
    text = (ROOT / "scripts/ci.sh").read_text(encoding="utf-8")
    stages = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('run_stage "') and '"' in line[11:]:
            stages.append(line.split('"')[1])
    return stages


def test_ci_has_all_required_stages_in_order():
    stages = _ci_stages()
    # The full twelve-stage sequence, in the exact order ci.sh runs it.  Deliberately
    # updated from the old ten-stage list (toolchain and post-push-verifier-unit
    # were missing), which let the approval gate drift from the real CI.
    required = [
        "toolchain", "python-unit", "mqtt-contract", "fake-ha-roundtrip",
        "frontend", "firmware-host", "firmware-hwcdc", "firmware-tinyusb",
        "deployment", "docs-notice", "post-push-verifier-unit", "public-audit",
    ]
    positions = [stages.index(stage) for stage in required]
    assert positions == sorted(positions)
    assert len(stages) == 12


def test_required_ci_stages_match_ci_sh_exactly():
    """The approval gate must never diverge from the CI stage list again."""
    assert REQUIRED_CI_STAGES == _ci_stages()


def test_firmware_versions_are_exactly_pinned():
    text = (ROOT / "scripts/compile-firmware.sh").read_text(encoding="utf-8")
    for pin in ("esp32:esp32@3.3.11", "GxEPD2@1.6.9", "ArduinoJson@7.4.3", "PubSubClient@2.8"):
        assert pin in text


def test_ci_unit_tests_post_push_verifier_without_requiring_live_artifacts():
    text = (ROOT / "scripts/ci.sh").read_text(encoding="utf-8")
    stage = 'run_stage "post-push-verifier-unit"'
    assert stage in text
    line = next(line for line in text.splitlines() if stage in line)
    assert "pytest tests/release/test_post_push_verification.py" in line
    assert "--candidate" not in line
    assert text.index(stage) < text.index('run_stage "public-audit"')