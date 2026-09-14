# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Verified create-or-update Home Assistant deployment orchestration.

P1.14 honesty rules:
* A deployment is reported ``verified`` only when the automation read-back AND
  every retained MQTT topic were actually verified.
* With an ``ApprovedPreview`` the exactly approved bytes are deployed — payloads
  are never regenerated from fresh HA state after approval.
* A failure after the automation write rolls the automation back (previous
  content, or a delete when it did not exist) and raises
  ``HADeploymentPartialError`` carrying the exact remaining changes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
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

#: The three retained topics, in canonical publication order.
RETAINED_TOPICS: tuple[str, ...] = (CATALOG_TOPIC, CURRENT_PAGE_TOPIC, KEYMAP_TOPIC)

#: A verifier receives the expected topic→payload map and returns the topics that
#: did NOT match the live broker (empty means fully verified).
MqttVerifier = Callable[[Mapping[str, str]], Sequence[str]]


def canonical_preview_manifest(file_sha256: dict[str, str]) -> str:
    """Serialize the canonical preview manifest (sorted keys, trailing newline).

    The manifest maps every preview filename to its file SHA-256 and is the exact
    byte stream the release digest hashes, so render and apply agree deterministically.
    """
    manifest = {"files": {name: file_sha256[name] for name in sorted(file_sha256)}}
    return json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


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


class HADeploymentPartialError(HADeploymentError):
    """Deployment failed after changes were applied; carries what remains changed."""

    def __init__(self, message: str, remaining: Sequence[str]) -> None:
        super().__init__(message)
        self.remaining: tuple[str, ...] = tuple(remaining)


@dataclass(frozen=True)
class ApprovedPreview:
    """The exact, operator-approved artifacts to deploy (P1.14)."""

    automation: dict[str, object]
    publications: tuple[tuple[str, str], ...]
    digest: str


@dataclass(frozen=True)
class HADeploymentResult:
    """Outcome of a completed deployment, including every retained publication."""

    created: bool
    automation_id: str
    published_topics: tuple[str, ...]
    automation_readback_verified: bool = True
    mqtt_verified: bool = False
    unverified_topics: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        """True only when the automation AND every retained topic were verified."""
        return self.automation_readback_verified and self.mqtt_verified


def load_approved_preview(preview_dir: Path) -> ApprovedPreview:
    """Load the rendered preview artifacts and recompute their digest (P1.14).

    The digest is computed over the ACTUAL bytes on disk, so a post-render change
    to the automation or the publications cannot be deployed under an old approval.
    """
    try:
        automation = json.loads((preview_dir / "automation.json").read_text(encoding="utf-8"))
        publications_doc = json.loads(
            (preview_dir / "mqtt-publications.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise HADeploymentError(f"live preview is incomplete or unreadable: {exc}") from None
    digest = preview_sha256(preview_dir)
    entries = publications_doc.get("publications")
    if not isinstance(entries, list) or not entries:
        raise HADeploymentError("live preview carries no MQTT publications")
    publications = tuple((str(entry["topic"]), str(entry["payload"])) for entry in entries)
    if tuple(topic for topic, _ in publications) != RETAINED_TOPICS:
        raise HADeploymentError(
            "live preview publications do not match the three retained MicroPad topics"
        )
    if not isinstance(automation, dict):
        raise HADeploymentError("live preview automation is not a JSON object")
    return ApprovedPreview(automation=automation, publications=publications, digest=digest)


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

    def _rollback_automation(self, previous: dict[str, object] | None) -> tuple[str, ...]:
        """Best-effort restore of the previous automation.

        Returns the remaining changes when the rollback itself failed.
        """
        try:
            if previous is None:
                self.client.delete_automation(AUTOMATION_ID)
            else:
                self.client.put_automation(AUTOMATION_ID, previous)
        except Exception:
            return (f"automation:{AUTOMATION_ID}",)
        return ()

    def deploy(
        self,
        config: AppConfig,
        *,
        approved: ApprovedPreview | None = None,
        mqtt_verifier: MqttVerifier | None = None,
    ) -> HADeploymentResult:
        """Verify auth, create-or-update, reload, read back, then publish retained topics.

        With ``approved`` the exactly approved automation and publications are
        deployed (no regeneration from fresh HA state).  ``mqtt_verifier`` reads the
        retained topics back; without it the retained topics are reported as
        published-but-unverified instead of claiming success.
        """
        if not self.client.settings.ha_token:
            raise HADeploymentError(
                "Home Assistant token is not configured; set settings.ha_token before deploying"
            )
        try:
            if approved is None:
                bundle = generate_bundle(config)
                automation: dict[str, object] = bundle.automation
            else:
                automation = approved.automation
            self.client.test_auth()
            previous = self.client.get_automation(AUTOMATION_ID)
            self.client.put_automation(AUTOMATION_ID, automation)
            self.client.call_service("automation", "reload", {})
            read_back = self.client.get_automation(AUTOMATION_ID)
            if not self._read_back_matches_sent(read_back, automation):
                remaining = self._rollback_automation(previous)
                detail = (
                    f"; rollback left {', '.join(remaining)}"
                    if remaining
                    else "; previous automation restored"
                )
                raise HADeploymentError(
                    "Home Assistant automation read-back did not match generated structure"
                    + detail
                )

            if approved is None:
                # UI path: reflect current HA state into the retained payloads.
                fresh_entities = self.client.list_entities()
                retained = generate_bundle(config, fresh_entities)
                publications: tuple[tuple[str, str], ...] = (
                    (CATALOG_TOPIC, retained.catalog_payload),
                    (CURRENT_PAGE_TOPIC, retained.home_payload),
                    (KEYMAP_TOPIC, retained.home_keymap_payload),
                )
            else:
                # Approved path: publish EXACTLY the reviewed bytes.
                publications = approved.publications

            published: list[str] = []
            for topic, payload in publications:
                try:
                    self.client.publish_mqtt(topic, payload, retain=True)
                except Exception as exc:
                    remaining = (*self._rollback_automation(previous), *published)
                    raise HADeploymentPartialError(
                        f"Home Assistant deployment failed while publishing {topic}",
                        remaining=remaining,
                    ) from exc
                published.append(topic)

            mqtt_verified = False
            unverified: tuple[str, ...] = tuple(published)
            if mqtt_verifier is not None:
                mismatched = tuple(
                    str(topic) for topic in mqtt_verifier(dict(publications))
                )
                if mismatched:
                    remaining = (*self._rollback_automation(previous), *mismatched)
                    raise HADeploymentPartialError(
                        "retained MQTT read-back did not match the approved publications",
                        remaining=remaining,
                    )
                mqtt_verified = True
                unverified = ()

            result = HADeploymentResult(
                previous is None,
                AUTOMATION_ID,
                tuple(published),
                True,
                mqtt_verified,
                unverified,
            )
            if self.evidence_sink is not None:
                self.evidence_sink(
                    {
                        "automation_id": AUTOMATION_ID,
                        "automation_sha256": self._sha256(
                            json.dumps(automation, ensure_ascii=False, sort_keys=True, allow_nan=False)
                        ),
                        "created": result.created,
                        "approved_digest": approved.digest if approved is not None else None,
                        "readback_verified": result.automation_readback_verified,
                        "mqtt_verified": result.mqtt_verified,
                        "unverified_topics": list(result.unverified_topics),
                        "request_order": [
                            "test_auth",
                            "get_automation",
                            "put_automation",
                            "automation.reload",
                            "get_automation",
                            *([] if approved is not None else ["list_entities"]),
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