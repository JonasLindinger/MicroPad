#!/usr/bin/env python3
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Verify retained MicroPad MQTT publications on a live broker.

Reads MQTT host/port/user from the git-ignored ``config.json`` and obtains the
password from that same ignored file in memory (never written anywhere). It
subscribes to the three retained MicroPad topics, requires ``retain is True``,
validates each payload against the contract JSON Schemas, and compares the
canonical payloads with the rendered ``mqtt-publications.json`` preview.

Only this script's secret-free evidence (topic, retain flag, schema result, and
payload SHA-256) is written to ``--output``. Exits 0 only when all three retained
publications match within ``--timeout`` seconds; otherwise fails-closed nonzero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from micropad.constants import CATALOG_TOPIC, CURRENT_PAGE_TOPIC, KEYMAP_TOPIC

_REPO = Path(__file__).resolve().parents[1]
_TOPICS = (CATALOG_TOPIC, CURRENT_PAGE_TOPIC, KEYMAP_TOPIC)
# Task 2 binding: each retained MicroPad topic validates against one contract schema.
_SCHEMA_FILE_BY_TOPIC = {
    CATALOG_TOPIC: "catalog.schema.json",
    CURRENT_PAGE_TOPIC: "page.schema.json",
    KEYMAP_TOPIC: "keymap.schema.json",
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_schemas() -> dict[str, dict]:
    """Load the Draft 2020-12 JSON Schema for each retained topic."""
    return {
        topic: json.loads((_REPO / "contracts" / filename).read_text(encoding="utf-8"))
        for topic, filename in _SCHEMA_FILE_BY_TOPIC.items()
    }


def _load_expected(preview_publications: Path) -> dict[str, str]:
    """Map topic → canonical expected payload from the rendered preview."""
    if not preview_publications.is_file():
        return {}
    data = json.loads(preview_publications.read_text(encoding="utf-8"))
    return {entry["topic"]: entry["payload"] for entry in data.get("publications", [])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config",
        default="config.json",
        help="git-ignored local config carrying the MQTT connection block (default: config.json)",
    )
    parser.add_argument(
        "--publications",
        default="build/live-ha-preview/mqtt-publications.json",
        help="rendered preview publications to compare against (default: build/live-ha-preview/mqtt-publications.json)",
    )
    parser.add_argument(
        "--output",
        default="build/live-mqtt-readback.json",
        help="secret-free read-back evidence destination (default: build/live-mqtt-readback.json)",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="seconds to wait for publications")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"error: config file not found: {config_path}")
        return 2
    settings = dict(json.loads(config_path.read_text(encoding="utf-8")).get("settings", {}))
    host = str(settings.get("mqtt_host", "")).strip()
    if not host:
        print("error: no MQTT endpoint configured (settings.mqtt_host); nothing to verify")
        return 2
    port = int(settings.get("mqtt_port") or 1883)
    username = str(settings.get("mqtt_user", ""))
    password = str(settings.get("mqtt_password", ""))  # in memory only; never persisted

    schemas = _load_schemas()
    expected = _load_expected(Path(args.publications))
    if not expected:
        print(
            "error: no preview publications to compare against — render the preview first "
            f"({args.publications})"
        )
        return 2

    try:
        import paho.mqtt.client as mqtt  # type: ignore[import-not-found]
    except ImportError:
        print("error: paho-mqtt is required to reach a live broker (pip install paho-mqtt)")
        return 2

    import jsonschema  # type: ignore[import-not-found]

    collected: dict[str, dict] = {}

    def on_message(client, userdata, message) -> None:  # type: ignore[no-untyped-def]
        topic = message.topic
        if topic not in _TOPICS:
            return
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = None
        schema = schemas.get(topic)
        schema_ok = bool(schema) and payload is not None
        if schema_ok:
            try:
                jsonschema.validate(payload, schema)
            except jsonschema.ValidationError:
                schema_ok = False
        collected[topic] = {
            "topic": topic,
            "retain": bool(message.retain),
            "schema_valid": schema_ok,
            "payload_sha256": _sha256(message.payload.decode("utf-8", errors="replace")),
            "expected_payload_sha256": _sha256(expected.get(topic, "")),
        }

    client = mqtt.Client()
    if username:
        client.username_pw_set(username, password)
    try:
        client.connect(host, port, keepalive=30)
        client.on_message = on_message
        for topic in _TOPICS:
            client.subscribe(topic)
        client.loop_start()
        deadline = time.monotonic() + max(0.0, args.timeout)
        while time.monotonic() < deadline:
            if all(topic in collected and collected[topic]["retain"] for topic in _TOPICS):
                break
            time.sleep(0.1)
        client.loop_stop()
        client.disconnect()
    except Exception as error:
        print(f"error: MQTT verification failed: {error}")
        return 1

    all_match = all(
        topic in collected
        and collected[topic]["retain"]
        and collected[topic]["schema_valid"]
        and collected[topic]["payload_sha256"] == collected[topic]["expected_payload_sha256"]
        for topic in _TOPICS
    )
    readback_doc = {
        "verified": all_match,
        "topics": [collected.get(topic) for topic in _TOPICS],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(readback_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not all_match:
        print("error: not all retained MQTT publications matched within the timeout")
        return 1
    print("verified: all retained MicroPad MQTT publications match the approved preview")
    return 0


if __name__ == "__main__":
    sys.exit(main())