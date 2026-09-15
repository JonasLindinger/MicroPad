# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Tests for the verified Home Assistant deployment orchestrator."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from micropad.ha_deploy import HADeploymentError, HomeAssistantDeployer
from micropad.models import EntitySummary, default_config


class RecordingHAClient:
    def __init__(
        self,
        existing: dict[str, object] | None = None,
        read_back: object = "generated",
        fail_at: str = "",
        ha_token: str | None = None,
    ) -> None:
        self.existing = existing
        self.read_back = read_back
        self.fail_at = fail_at
        self.ha_token = "configured-token" if ha_token is None else ha_token
        self.operations: list[str] = []
        self.generated: dict[str, object] | None = None
        self.read_count = 0
        self.published: list[tuple[str, bool]] = []
        self.settings = SimpleNamespace(ha_token=self.ha_token)

    def _record(self, operation: str) -> None:
        self.operations.append(operation)
        if self.fail_at == operation:
            raise RuntimeError(operation)

    def test_auth(self) -> dict[str, object]:
        self._record("test_auth")
        return {"message": "ok"}

    def get_automation(self, automation_id: str) -> dict[str, object] | None:
        self.read_count += 1
        self._record("get_automation")
        if self.read_count == 2 and self.fail_at == "get_automation:second":
            raise RuntimeError("get_automation:second")
        if self.read_count == 1:
            return self.existing
        return self.generated if self.read_back == "generated" else self.read_back

    def put_automation(self, automation_id: str, automation: dict[str, object]) -> None:
        self._record("put_automation")
        self.generated = automation

    def call_service(
        self, domain: str, service: str, data: dict[str, object]
    ) -> list[dict[str, object]]:
        self._record(f"{domain}.{service}")
        return []

    def list_entities(self) -> list[EntitySummary]:
        self._record("list_entities")
        return [
            EntitySummary(
                entity_id="sensor.test", friendly_name="Test", domain="sensor", state="ready"
            )
        ]

    def publish_mqtt(self, topic: str, payload: str, retain: bool = True) -> None:
        self._record(f"publish:{topic}")
        self.published.append((topic, retain))


def test_create_path_reads_writes_reloads_verifies_then_publishes() -> None:
    client = RecordingHAClient(existing=None, read_back="generated")
    result = HomeAssistantDeployer(client).deploy(default_config())
    assert result.created is True
    assert client.operations == [
        "test_auth",
        "get_automation",
        "put_automation",
        "automation.reload",
        "get_automation",
        "list_entities",
        "publish:micropad/pages/all",
        "publish:micropad/page/current",
        "publish:micropad/keymap",
    ]
    assert result.published_topics == (
        "micropad/pages/all",
        "micropad/page/current",
        "micropad/keymap",
    )


def test_update_path_reports_not_created() -> None:
    client = RecordingHAClient(existing={"id": "micropad_controller"}, read_back="generated")
    assert HomeAssistantDeployer(client).deploy(default_config()).created is False


def test_mismatched_read_back_prevents_all_mqtt_publications() -> None:
    client = RecordingHAClient(existing=None, read_back={"id": "different"})
    with pytest.raises(HADeploymentError, match="did not match"):
        HomeAssistantDeployer(client).deploy(default_config())
    assert not [operation for operation in client.operations if operation.startswith("publish:")]


def test_mid_sequence_failures_are_wrapped_as_hadeploymenterror_with_cause() -> None:
    client = RecordingHAClient(fail_at="test_auth")
    with pytest.raises(HADeploymentError) as excinfo:
        HomeAssistantDeployer(client).deploy(default_config())
    cause = excinfo.value.__cause__
    assert cause is not None
    assert isinstance(cause, RuntimeError)
    assert cause.args == ("test_auth",)
    assert client.published == []


@pytest.mark.parametrize(
    "failure",
    ["test_auth", "put_automation", "automation.reload", "get_automation:second", "list_entities"],
)
def test_each_prepublication_failure_prevents_retained_messages(failure: str) -> None:
    client = RecordingHAClient(fail_at=failure)
    with pytest.raises(RuntimeError):
        HomeAssistantDeployer(client).deploy(default_config())
    assert not [operation for operation in client.operations if operation.startswith("publish:")]
    assert client.published == []


def test_empty_token_fails_clearly_before_any_request() -> None:
    client = RecordingHAClient(ha_token="")
    with pytest.raises(HADeploymentError, match="token"):
        HomeAssistantDeployer(client).deploy(default_config())
    assert client.operations == []


def test_publications_are_retained_for_catalog_current_and_keymap() -> None:
    client = RecordingHAClient(existing=None)
    HomeAssistantDeployer(client).deploy(default_config())
    assert client.published == [
        ("micropad/pages/all", True),
        ("micropad/page/current", True),
        ("micropad/keymap", True),
    ]


# --- P1.14: approved-preview pinning, rollback, honest verification -----------


class RollbackRecordingClient(RecordingHAClient):
    """Recording client that also records deletes and can fail a chosen publish."""

    def __init__(self, *args, fail_publish_topic: str = "", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fail_publish_topic = fail_publish_topic
        self.deleted: list[str] = []
        self.payloads: list[tuple[str, str]] = []

    def publish_mqtt(self, topic, payload, retain=True):
        self._record(f"publish:{topic}")
        if topic == self.fail_publish_topic:
            raise RuntimeError(f"publish:{topic}")
        self.published.append((topic, retain))
        self.payloads.append((topic, payload))

    def delete_automation(self, automation_id: str) -> None:
        self._record("delete_automation")
        self.deleted.append(automation_id)


def _approved_preview(tmp_path):
    """Build a realistic approved preview from the real generator."""
    from micropad.generator import generate_bundle
    from micropad.ha_deploy import RETAINED_TOPICS, ApprovedPreview

    bundle = generate_bundle(default_config())
    payloads = (
        (RETAINED_TOPICS[0], bundle.catalog_payload),
        (RETAINED_TOPICS[1], bundle.home_payload),
        (RETAINED_TOPICS[2], bundle.home_keymap_payload),
    )
    return ApprovedPreview(
        automation=bundle.automation, publications=payloads, digest="a" * 64
    )


def test_approved_preview_publishes_exactly_the_approved_bytes(tmp_path) -> None:
    client = RollbackRecordingClient(existing=None)
    approved = _approved_preview(tmp_path)
    # read_back must match the approved automation for verification to pass.
    client.read_back = approved.automation
    HomeAssistantDeployer(client).deploy(default_config(), approved=approved)
    assert client.payloads == list(approved.publications)
    # The approved path never regenerates payloads from fresh HA state.
    assert "list_entities" not in client.operations


def test_read_back_mismatch_rolls_back_to_previous_automation() -> None:
    previous = {"id": "micropad_controller", "alias": "Previous"}
    client = RollbackRecordingClient(existing=previous, read_back={"id": "different"})
    with pytest.raises(HADeploymentError, match="did not match"):
        HomeAssistantDeployer(client).deploy(default_config())
    assert "put_automation" in client.operations
    # The previous automation is restored (put_automation is called twice).
    assert client.operations.count("put_automation") == 2
    assert not [op for op in client.operations if op.startswith("publish:")]


def test_read_back_mismatch_on_first_create_deletes_the_new_automation() -> None:
    client = RollbackRecordingClient(existing=None, read_back={"id": "different"})
    with pytest.raises(HADeploymentError, match="did not match"):
        HomeAssistantDeployer(client).deploy(default_config())
    assert client.deleted == ["micropad_controller"]


def test_publish_failure_rolls_back_and_reports_remaining_changes() -> None:
    from micropad.ha_deploy import HADeploymentPartialError

    client = RollbackRecordingClient(existing=None, fail_publish_topic="micropad/page/current")
    with pytest.raises(HADeploymentPartialError) as excinfo:
        HomeAssistantDeployer(client).deploy(default_config())
    # The automation write was rolled back (delete, since it was a create) ...
    assert client.deleted == ["micropad_controller"]
    # ... and the exact remaining change is reported (the topic that got published).
    assert excinfo.value.remaining == ("micropad/pages/all",)


def test_unverified_retained_topics_are_never_reported_as_verified() -> None:
    client = RollbackRecordingClient(existing=None)
    result = HomeAssistantDeployer(client).deploy(default_config())
    assert result.automation_readback_verified is True
    assert result.mqtt_verified is False  # no verifier configured
    assert result.verified is False
    assert set(result.unverified_topics) == {
        "micropad/pages/all",
        "micropad/page/current",
        "micropad/keymap",
    }


def test_mqtt_verifier_mismatch_rolls_back_and_reports_partial_failure() -> None:
    from micropad.ha_deploy import HADeploymentPartialError

    client = RollbackRecordingClient(existing=None)
    mismatched = ["micropad/keymap"]

    def verifier(expected: dict[str, str]) -> list[str]:
        return mismatched

    with pytest.raises(HADeploymentPartialError) as excinfo:
        HomeAssistantDeployer(client).deploy(default_config(), mqtt_verifier=verifier)
    assert excinfo.value.remaining == ("micropad/keymap",)
    assert client.deleted == ["micropad_controller"]


def test_mqtt_verifier_success_marks_the_deployment_verified() -> None:
    client = RollbackRecordingClient(existing=None)
    seen: dict[str, str] = {}

    def verifier(expected: dict[str, str]) -> list[str]:
        seen.update(expected)
        return []

    result = HomeAssistantDeployer(client).deploy(default_config(), mqtt_verifier=verifier)
    assert result.mqtt_verified is True
    assert result.verified is True
    assert result.unverified_topics == ()
    assert set(seen) == {"micropad/pages/all", "micropad/page/current", "micropad/keymap"}


def test_load_approved_preview_reads_the_rendered_artifacts(tmp_path) -> None:
    from micropad.ha_deploy import load_approved_preview, preview_sha256
    from scripts.render_live_ha_preview import main as render_main

    config_path = tmp_path / "config.json"
    config_path.write_text(default_config().model_dump_json(by_alias=True), encoding="utf-8")
    preview_dir = tmp_path / "preview"
    assert render_main(["--config", str(config_path), "--output", str(preview_dir)]) == 0

    approved = load_approved_preview(preview_dir)
    assert approved.digest == preview_sha256(preview_dir)
    assert [topic for topic, _ in approved.publications] == [
        "micropad/pages/all",
        "micropad/page/current",
        "micropad/keymap",
    ]


def test_load_approved_preview_refuses_an_incomplete_preview(tmp_path) -> None:
    from micropad.ha_deploy import load_approved_preview

    (tmp_path / "automation.json").write_text("{}", encoding="utf-8")
    with pytest.raises(HADeploymentError, match="incomplete"):
        load_approved_preview(tmp_path)
