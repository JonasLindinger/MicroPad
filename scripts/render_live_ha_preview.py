#!/usr/bin/env python3
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Render a deterministic, reviewable live-HA preview bundle.

Produces ``automation.json``, ``automation.yaml``, ``mqtt-publications.json`` and a
hash-locked ``release.sha256`` under ``--output``. The digest is computed from a
canonical manifest over the three content files, so rendering the same config twice
yields byte-identical output; ``apply_live_ha_preview.py`` will refuse to deploy
unless an operator approves exactly this digest.

This script performs no network access and never touches a live Home Assistant.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from micropad.constants import CATALOG_TOPIC, CURRENT_PAGE_TOPIC, KEYMAP_TOPIC
from micropad.generator import automation_yaml, generate_bundle
from micropad.ha_deploy import PREVIEW_FILES, preview_sha256
from micropad.models import parse_config


def _write_publications(output_dir: Path, bundle) -> None:
    """Record the three retained, deterministic preview publications."""
    publications = [
        {
            "topic": CATALOG_TOPIC,
            "retain": True,
            "qos": 0,
            "payload": bundle.catalog_payload,
            "payload_sha256": hashlib.sha256(bundle.catalog_payload.encode("utf-8")).hexdigest(),
        },
        {
            "topic": CURRENT_PAGE_TOPIC,
            "retain": True,
            "qos": 0,
            "payload": bundle.home_payload,
            "payload_sha256": hashlib.sha256(bundle.home_payload.encode("utf-8")).hexdigest(),
        },
        {
            "topic": KEYMAP_TOPIC,
            "retain": True,
            "qos": 0,
            "payload": bundle.home_keymap_payload,
            "payload_sha256": hashlib.sha256(
                bundle.home_keymap_payload.encode("utf-8")
            ).hexdigest(),
        },
    ]
    encoded = json.dumps({"publications": publications}, indent=2, ensure_ascii=False)
    (output_dir / "mqtt-publications.json").write_text(encoded + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="path to validated config JSON")
    parser.add_argument("--output", required=True, help="preview output directory")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_file():
        parser.error(f"config file not found: {config_path}")
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = parse_config(json.loads(config_path.read_text(encoding="utf-8")))
    bundle = generate_bundle(config)

    (output_dir / "automation.json").write_text(
        json.dumps(bundle.automation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "automation.yaml").write_text(automation_yaml(bundle.automation), encoding="utf-8")
    _write_publications(output_dir, bundle)

    digest = preview_sha256(output_dir)
    (output_dir / "release.sha256").write_text(digest + "\n", encoding="utf-8")
    print(
        f"wrote preview to {output_dir} ({', '.join(PREVIEW_FILES)}, release.sha256); "
        f"sha256={digest}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())