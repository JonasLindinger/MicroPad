#!/usr/bin/env python3
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Hash-locked live Home Assistant deployment gate.

Refuses to touch a live Home Assistant unless the operator explicitly passes an
``--approved-sha256`` digest that matches the rendered preview bundle. The digest
is verified *before* any network connection is opened, so an un-reviewed or stale
preview can never be deployed. Requires ``MICROPAD_HA_TOKEN`` from the environment;
credentials are never embedded or written to disk.

Run only against a configured live endpoint you intend to modify. This is the
explicit operator gate — the preview must be rendered and reviewed first.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from micropad.constants import AUTOMATION_ID
from micropad.generator import canonical_automation, generate_bundle
from micropad.ha_client import HAClientError, HomeAssistantClient
from micropad.ha_deploy import HADeploymentError, HomeAssistantDeployer, preview_sha256
from micropad.models import parse_config

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _sha256_bytes(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preview", required=True, help="preview directory from render_live_ha_preview")
    parser.add_argument("--config", required=True, help="validated config JSON (live settings)")
    parser.add_argument(
        "--approved-sha256",
        required=True,
        metavar="HEX64",
        help="operator-approved digest of the rendered preview release.sha256",
    )
    parser.add_argument(
        "--readback-output",
        default="build/live-ha-readback.json",
        help="secret-free read-back evidence destination (default: build/live-ha-readback.json)",
    )
    args = parser.parse_args(argv)

    approved = args.approved_sha256.strip().lower()
    if not _HEX64.fullmatch(approved):
        print("error: --approved-sha256 must be a 64-character lowercase hex digest")
        return 2

    preview_dir = Path(args.preview)
    try:
        computed = preview_sha256(preview_dir)
    except HADeploymentError as error:
        print(f"error: {error}")
        return 2
    if computed != approved:
        print(
            "error: approved hash does not match the rendered preview "
            f"(approved={approved}, preview={computed}); refusing to open a network connection"
        )
        return 1

    token = os.environ.get("MICROPAD_HA_TOKEN", "").strip()
    if not token:
        print("error: MICROPAD_HA_TOKEN is not set; refusing to deploy without credentials")
        return 2

    config_path = Path(args.config)
    if not config_path.is_file():
        parser.error(f"config file not found: {config_path}")
    config = parse_config(json.loads(config_path.read_text(encoding="utf-8")))
    config.settings.ha_token = token

    # The approved preview locks the automation an operator already reviewed; if the
    # config was edited since rendering, do not push an unreviewed automation live.
    bundle = generate_bundle(config)
    preview_automation = json.loads((preview_dir / "automation.json").read_text(encoding="utf-8"))
    if canonical_automation(bundle.automation) != canonical_automation(preview_automation):
        print(
            "error: config no longer matches the approved preview automation; "
            "re-render and re-approve before applying"
        )
        return 1

    evidence: dict[str, object] = {}
    client = HomeAssistantClient(config.settings)
    deployer = HomeAssistantDeployer(client, evidence_sink=evidence.update)
    try:
        result = deployer.deploy(config)
    except HADeploymentError as error:
        print(f"error: deployment failed: {error}")
        return 1

    try:
        readback = client.get_automation(AUTOMATION_ID)
    except HAClientError as error:
        print(f"error: live read-back failed: {error}")
        return 1
    readback_matches = readback is not None and canonical_automation(readback) == canonical_automation(
        bundle.automation
    )
    if not readback_matches:
        print("error: live read-back structure does not match the approved automation")
        return 1

    readback_doc: dict[str, object] = {
        "automation_id": AUTOMATION_ID,
        "deploy_created": result.created,
        "published_topics": list(result.published_topics),
        "deploy_evidence": evidence,
        "live_readback_sha256": _sha256_bytes(json.dumps(readback, sort_keys=True, ensure_ascii=False)),
        "live_readback_matches_approved": True,
    }
    readback_path = Path(args.readback_output)
    readback_path.parent.mkdir(parents=True, exist_ok=True)
    readback_path.write_text(
        json.dumps(readback_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"applied approved automation to {config.settings.ha_url} "
        f"(created={result.created}); evidence written to {readback_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())