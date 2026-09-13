# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Verified create-or-update Home Assistant deployment orchestration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from micropad.constants import (
    AUTOMATION_ID,
    CATALOG_TOPIC,
    CURRENT_PAGE_TOPIC,
    KEYMAP_TOPIC,
)
from micropad.generator import canonical_automation, generate_bundle
from micropad.ha_client import HomeAssistantClient
from micropad.models import AppConfig

PREVIEW_FILES: tuple[str, ...] = (
    "automation.json",
    "automation.yaml",
    "mqtt-publications.json",
)


def canonical_preview_manifest(file_sha256: dict[str, str]) -> str:
    """Serialize the canonical preview manifest (sorted keys, trailing newline).

    The manifest maps every preview filename to its file SHA-256 and is the exact
    byte stream the release digest hashes, so render and apply agree deterministically.
    """
    manifest = {"files": {name: file_sha256[name] for name in sorted(file_sha256)}}
    return json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def preview_sha256(preview_dir: Path) -> str:
    """Return the SHA-256 of the canonical manifest over the preview's content files."""
    file_sha256 = {
        name: hashlib.sha256((preview_dir / name).read_bytes()).hexdigest()
        for name in PREVIEW_FILES
        if (preview_dir / name).is_file()
    }
    if set(file_sha256) != set(PREVIEW_FILES):
        missing = sorted(set(PREVIEW_FILES) - set(file_sha256))
        raise HADeploymentError(f"preview directory is incomplete; missing: {', '.join(missing)}")
    return hashlib.sha256(canonical_preview_manifest(file_sha256).encode("utf-8")).hexdigest()


class HADeploymentError(RuntimeError):
    """Raised when a deployment step fails or its read-back cannot be verified."""


@dataclass(frozen=True)
class HADeploymentResult:
    """Outcome of a completed deployment, including every retained publication."""

    created: bool
    automation_id: str
    published_topics: tuple[str, ...]


class HomeAssistantDeployer:
    """Deploy a validated controller to Home Assistant with verified read-back."""

    def __init__(
        self,
        client: HomeAssistantClient,
        evidence_sink: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.client = client
        self.evidence_sink = evidence_sink

    @staticmethod
    def _read_back_matches_sent(actual: object, sent: dict[str, object]) -> bool:
        """Compare the exact relevant read-back structure to what was written."""
        if not isinstance(actual, dict):
            return False
        try:
            return canonical_automation(actual) == canonical_automation(sent)
        except (KeyError, TypeError):
            return False

    @staticmethod
    def _sha256(payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def deploy(self, config: AppConfig) -> HADeploymentResult:
        """Verify auth, create-or-update, reload, read back, then publish retained topics."""
        if not self.client.settings.ha_token:
            raise HADeploymentError(
                "Home Assistant token is not configured; set settings.ha_token before deploying"
            )
        try:
            bundle = generate_bundle(config)
            self.client.test_auth()
            existing = self.client.get_automation(AUTOMATION_ID)
            self.client.put_automation(AUTOMATION_ID, bundle.automation)
            self.client.call_service("automation", "reload", {})
            read_back = self.client.get_automation(AUTOMATION_ID)
            if not self._read_back_matches_sent(read_back, bundle.automation):
                raise HADeploymentError(
                    "Home Assistant automation read-back did not match generated structure"
                )
            fresh_entities = self.client.list_entities()
            retained_bundle = generate_bundle(config, fresh_entities)
            publications: tuple[tuple[str, str], ...] = (
                (CATALOG_TOPIC, retained_bundle.catalog_payload),
                (CURRENT_PAGE_TOPIC, retained_bundle.home_payload),
                (KEYMAP_TOPIC, retained_bundle.home_keymap_payload),
            )
            for topic, payload in publications:
                self.client.publish_mqtt(topic, payload, retain=True)
            result = HADeploymentResult(
                existing is None,
                AUTOMATION_ID,
                tuple(topic for topic, _ in publications),
            )
            if self.evidence_sink is not None:
                self.evidence_sink(
                    {
                        "automation_id": AUTOMATION_ID,
                        "automation_sha256": self._sha256(
                            json.dumps(bundle.automation, ensure_ascii=False, sort_keys=True)
                        ),
                        "created": result.created,
                        "readback_verified": True,
                        "request_order": [
                            "test_auth",
                            "get_automation",
                            "put_automation",
                            "automation.reload",
                            "get_automation",
                            "list_entities",
                            *(f"publish:{topic}" for topic, _ in publications),
                        ],
                        "publications": [
                            {"topic": topic, "retain": True, "payload_sha256": self._sha256(payload)}
                            for topic, payload in publications
                        ],
                    }
                )
            return result
        except HADeploymentError:
            raise
        except Exception as exc:
            raise HADeploymentError("Home Assistant deployment failed mid-sequence") from exc
