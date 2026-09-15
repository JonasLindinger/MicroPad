#!/usr/bin/env python3
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Hash-locked live Home Assistant deployment gate.

Refuses to touch a live Home Assistant unless the operator explicitly passes an
``--approved-sha256`` digest that matches the rendered preview bundle. The digest
is verified *before* any network connection is opened, so an un-reviewed or stale
preview can never be deployed. Requires ``MICROPAD_HA_TOKEN`` from the environment;
credentials are never embedded or written to disk.

Run only against a configured live endpoint you intend to modify. This is the
explicit operator gate — the preview must be rendered and reviewed first.

Exit codes: 0 = applied AND fully verified; 1 = refused/failed; 2 = usage error;
3 = applied but the retained MQTT topics were not read back (pass --mqtt-verify
to read them back and reach a verified deployment).
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
from micropad.ha_deploy import (
    HADeploymentError,
    HomeAssistantDeployer,
    load_approved_preview,
    preview_sha256,
)
from micropad.models import parse_config

# Running `python scripts/apply_live_ha_preview.py` puts scripts/ (not the repo
# root) on sys.path; add the root so the sibling verifier module is importable.
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.verify_live_mqtt import build_retained_verifier  # noqa: E402

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
    parser.add_argument(
        "--mqtt-verify",
        action="store_true",
        help=(
            "read the three retained topics back from the broker (settings.mqtt_host in "
            "the config) and require them to match the approved preview; without this flag "
            "the deployment is reported as published-but-unverified and exits nonzero"
        ),
    )
    parser.add_argument(
        "--mqtt-timeout", type=float, default=15.0, help="seconds to wait for MQTT read-back"
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
    approved = load_approved_preview(preview_dir)
    bundle = generate_bundle(config)
    preview_automation = json.loads((preview_dir / "automation.json").read_text(encoding="utf-8"))
    if canonical_automation(bundle.automation) != canonical_automation(preview_automation):
        print(
            "error: config no longer matches the approved preview automation; "
            "re-render and re-approve before applying"
        )
        return 1

    # P1.14: publish exactly the approved bytes; optionally read the retained
    # topics back from the broker before claiming the deployment verified.
    mqtt_verifier = None
    if args.mqtt_verify:
        raw_settings = json.loads(config_path.read_text(encoding="utf-8")).get("settings", {})
        host = str(raw_settings.get("mqtt_host", "")).strip()
        if not host:
            print("error: --mqtt-verify needs settings.mqtt_host in the config")
            return 2
        mqtt_verifier = build_retained_verifier(
            host,
            int(raw_settings.get("mqtt_port") or 1883),
            str(raw_settings.get("mqtt_user", "")),
            str(raw_settings.get("mqtt_password", "")),
            args.mqtt_timeout,
        )

    evidence: dict[str, object] = {}
    client = HomeAssistantClient(config.settings)
    deployer = HomeAssistantDeployer(client, evidence_sink=evidence.update)
    try:
        result = deployer.deploy(config, approved=approved, mqtt_verifier=mqtt_verifier)
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
        "approved_digest": approved.digest,
        "deployment_verified": result.verified,
        "mqtt_verified": result.mqtt_verified,
        "unverified_topics": list(result.unverified_topics),
        "deploy_evidence": evidence,
        "live_readback_sha256": _sha256_bytes(json.dumps(readback, sort_keys=True, ensure_ascii=False)),
        "live_readback_matches_approved": True,
    }
    readback_path = Path(args.readback_output)
    readback_path.parent.mkdir(parents=True, exist_ok=True)
    readback_path.write_text(
        json.dumps(readback_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if not result.verified:
        unverified = ", ".join(result.unverified_topics) or "none"
        print(
            "WARNING: the approved automation is applied and verified, but the retained "
            f"MQTT topics were NOT read back ({unverified}); the deployment is therefore "
            "NOT reported as verified. Re-run with --mqtt-verify against the broker to "
            f"confirm them. Evidence written to {readback_path}"
        )
        return 3
    print(
        f"applied and verified the approved automation to {config.settings.ha_url} "
        f"(created={result.created}, retained topics read back); evidence written to {readback_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())