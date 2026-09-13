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
