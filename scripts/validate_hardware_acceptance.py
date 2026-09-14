#!/usr/bin/env python3
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Validate a physical MicroPad hardware acceptance record against the strict schema.

The operator-filled local record ``release/local-hardware-acceptance.json`` is
never committed.  This tool validates it strictly and — only when it is fully
valid (every required check present, both ``true``, and backed by evidence) —
writes a **redacted** summary to ``build/evidence/hardware-acceptance-summary.json``
with only check IDs, booleans, timestamps, the firmware SHA-256, and per-check
evidence SHA-256s.

No operator, Wi-Fi, MQTT, Home Assistant, broker hostname, device entity, or other
non-acceptance data can enter the summary: the strict schema
(``release/hardware-acceptance.schema.json``, ``additionalProperties: false``
throughout) rejects any such field before a summary is written.

Exit codes:
    0  record validated clean and the redacted summary was written
    1  record invalid (each problem listed on stderr) or unreadable

Usage:
    scripts/validate_hardware_acceptance.py \\
        --input release/local-hardware-acceptance.json \\
        --output build/evidence/hardware-acceptance-summary.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator

_REPO = Path(__file__).resolve().parents[1]
SCHEMA_PATH = _REPO / "release" / "hardware-acceptance.schema.json"

# ISO-8601 with a mandatory timezone expressed as ``Z`` or an explicit offset.
_UTC_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _describe(error: object) -> str:
    location = "/".join(str(part) for part in error.absolute_path)  # type: ignore[attr-defined]
    message = error.message  # type: ignore[attr-defined]
    return f"{location}: {message}" if location else message


def _dedupe(errors: list[str]) -> list[str]:
    """Preserve order while removing duplicate schema messages."""
    seen: set[str] = set()
    unique: list[str] = []
    for error in errors:
        if error not in seen:
            seen.add(error)
            unique.append(error)
    return unique


def _is_utc_iso(value: object) -> bool:
    if not isinstance(value, str) or not _UTC_DATETIME_RE.match(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def validate_report(report: dict) -> list[str]:
    """Validate ``report`` against the strict schema; return a list of problems.

    An empty list means the record is fully valid (every required check present
    and ``true``, with well-formed evidence).  Never raises for a malformed dict:
    schema errors are collected and returned as human-readable messages.
    """
    errors = [_describe(e) for e in _schema_validator().iter_errors(report)]

    # UTC timestamps are a format constraint, enforced complementarily because
    # JSON-Schema ``format: date-time`` is not checked by the default validator.
    for field in ("started_at", "completed_at"):
        if field in report and not _is_utc_iso(report[field]):
            errors.append(f"{field}: must be a UTC ISO-8601 timestamp")
    evidence = report.get("evidence")
    if isinstance(evidence, list):
        for index, entry in enumerate(evidence):
            captured = None
            if isinstance(entry, dict):
                captured = entry.get("captured_at")
            if captured is not None and not _is_utc_iso(captured):
                errors.append(f"evidence[{index}].captured_at: must be a UTC ISO-8601 timestamp")

    return _dedupe(errors)


def redact_summary(report: dict) -> dict:
    """Build the redacted summary from a validated report — acceptance data only.

    Deliberately omits every non-acceptance field (operator, Wi-Fi, MQTT, Home
    Assistant, broker host, device entity) even if one somehow passed validation.
    """
    return {
        "schema_version": report["schema_version"],
        "board_id": report["board_id"],
        "firmware_sha256": report["firmware_sha256"],
        "started_at": report["started_at"],
        "completed_at": report["completed_at"],
        "checks": {cid: bool(result) for cid, result in report["checks"].items()},
        "evidence_sha256": {
            entry["check_id"]: entry["sha256"] for entry in report["evidence"]
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--input", default="release/local-hardware-acceptance.json",
        help="path to the operator-filled local acceptance record (never committed)",
    )
    parser.add_argument(
        "--output", default="build/evidence/hardware-acceptance-summary.json",
        help="path to the redacted summary produced on success",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    try:
        report = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {input_path}: {exc}", file=sys.stderr)
        return 1

    errors = validate_report(report)
    if errors:
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        print(
            f"hardware acceptance record is incomplete: {len(errors)} problem(s)",
            file=sys.stderr,
        )
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(redact_summary(report), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"acceptance clean; redacted summary -> {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())