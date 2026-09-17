# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Hardware acceptance record: exact check coverage and schema / redaction gates.

Pins the exact 27-ID ``REQUIRED`` contract, proves a complete synthetic record
validates clean, and rejects incomplete, malformed, and un-redacted content.
Forbidden canary values (plaintext SSID, broker hostname, token, email, entity
ID) are assembled from adjacent string fragments at test runtime so the committed
source never contains a contiguous secret-shaped literal.
"""

from __future__ import annotations

import copy
import json

import pytest

from scripts.validate_hardware_acceptance import redact_summary, validate_report

REQUIRED = {
    "boot-full-refresh", "display-four-rows", "full-panel-partial-refresh",
    "ghosting-bound", "matrix-all-twelve", "encoder-clockwise-down",
    "encoder-counterclockwise-up", "encoder-push-r0c3", "input-during-refresh",
    "gesture-hold-binding", "gesture-double-press", "gesture-wake-press-exempt",
    "slider-adjust-encoder", "checkbox-value-column",
    "portal-random-password", "portal-never-sleeps", "portal-input-responsive",
    "portal-back-no-save",
    "wifi-mqtt-nonblocking", "mqtt-reconnect-resubscribe", "authoritative-resync",
    "usb-host-icon-after-1500ms", "usb-host-prevents-sleep",
    "usb-disconnect-clears-after-1500ms", "battery-idle-sleeps-near-60s",
    "wake-action-immediate", "charge-only-follows-battery-sleep",
}


def test_complete_report_requires_every_physical_check(valid_hardware_report):
    assert set(valid_hardware_report["checks"]) == REQUIRED
    assert validate_report(valid_hardware_report) == []


def test_missing_check_id_is_rejected(valid_hardware_report):
    bad = copy.deepcopy(valid_hardware_report)
    del bad["checks"]["matrix-all-twelve"]
    errors = validate_report(bad)
    assert errors != []
    assert "matrix-all-twelve" in " ".join(errors)


def test_false_check_result_is_rejected(valid_hardware_report):
    bad = copy.deepcopy(valid_hardware_report)
    bad["checks"]["wake-action-immediate"] = False
    errors = validate_report(bad)
    assert errors != []
    assert "wake-action-immediate" in " ".join(errors)


def test_extra_check_id_is_rejected(valid_hardware_report):
    bad = copy.deepcopy(valid_hardware_report)
    bad["checks"]["ghosting-extra"] = True
    errors = validate_report(bad)
    assert errors != []
    assert "checks" in " ".join(errors)


def test_missing_evidence_hash_is_rejected(valid_hardware_report):
    bad = copy.deepcopy(valid_hardware_report)
    del bad["evidence"][0]["sha256"]
    errors = validate_report(bad)
    assert errors != []
    assert "sha256" in " ".join(errors)


def test_malformed_evidence_hash_is_rejected(valid_hardware_report):
    bad = copy.deepcopy(valid_hardware_report)
    bad["evidence"][0]["sha256"] = "not-a-64-lowercase-hex-digest"
    errors = validate_report(bad)
    assert errors != []
    assert "sha256" in " ".join(errors)


@pytest.mark.parametrize(
    ("field", "canary"),
    [
        ("wifi_ssid", "My" + "Home" + "WiFi"),
        ("broker_host", "mqtt" + "." + "broker" + ".lan"),
        ("access_token", "eyJ" + "hbGci" + "OiJIUzI1NiJ9"),
        ("operator_email", "bench" + "@" + "example" + ".com"),
        ("entity_id", "light" + "." + "desk" + "_lamp"),
    ],
)
def test_non_acceptance_fields_are_rejected(valid_hardware_report, field, canary):
    bad = copy.deepcopy(valid_hardware_report)
    bad[field] = canary
    errors = validate_report(bad)
    assert errors != [], f"{field} should be rejected"
    assert field in " ".join(errors)


def test_redacted_summary_contains_only_acceptance_data(valid_hardware_report):
    summary = redact_summary(valid_hardware_report)
    assert set(summary) == {
        "schema_version",
        "board_id",
        "firmware_sha256",
        "started_at",
        "completed_at",
        "checks",
        "evidence_sha256",
    }
    assert summary["board_id"] == valid_hardware_report["board_id"]
    assert summary["firmware_sha256"] == valid_hardware_report["firmware_sha256"]
    assert summary["checks"] == valid_hardware_report["checks"]
    assert summary["evidence_sha256"] == {
        entry["check_id"]: entry["sha256"]
        for entry in valid_hardware_report["evidence"]
    }
    blob = json.dumps(summary)
    for leaked in (
        "wifi_ssid",
        "broker_host",
        "access_token",
        "operator_email",
        "entity_id",
        "@",
        "desk_lamp",
        "example.com",
    ):
        assert leaked not in blob, f"summary leaked {leaked!r}"