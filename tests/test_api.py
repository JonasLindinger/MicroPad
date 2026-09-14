# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""API tests for the Flask configuration, metadata, validation, and generation slice."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from micropad.app import create_app
from micropad.config_store import ConfigStore
from micropad.constants import ACTIONS
from micropad.ha_client import HAClientError
from micropad.ha_deploy import HADeploymentError
from micropad.models import EntitySummary
from micropad.ssh_upload import SSHUploadError


def test_health_and_index_cache_policy(client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json == {"ok": True, "service": "micropad-configurator"}
    assert response.headers["Cache-Control"] == "no-store"


def test_meta_exposes_exact_protocol_values(client) -> None:
    response = client.get("/api/meta")
    assert response.status_code == 200
    assert len(response.json["key_ids"]) == 14
    assert {item["id"] for item in response.json["actions"]} == ACTIONS
    assert [item["group"] for item in response.json["actions"][:4]] == ["Navigation"] * 4
    assert len(response.json["default_keymap"]) == 14
    assert {item["id"] for item in response.json["templates"]} == {
        "spotify",
        "discord",
        "lights",
        "generic-media",
    }
    assert response.json["home_page_id"] == "home"
    assert response.json["automation_id"] == "micropad_controller"


def test_post_config_persists_valid_data_but_preserves_blank_secrets(
    client,
    store: ConfigStore,
) -> None:
    existing = store.load().model_copy(deep=True)
    existing.settings.ha_token = "saved-token"  # noqa: S105
    store.save(existing)
    payload = client.get("/api/config").json
    payload["pages"][0]["title"] = "Changed"
    payload["settings"]["ha_token"] = ""
    response = client.post("/api/config", json=payload)
    assert response.status_code == 200
    assert store.load().pages[0].title == "Changed"
    assert store.load().settings.ha_token == "saved-token"  # noqa: S105


def test_generate_returns_yaml_and_three_payload_previews(client) -> None:
    response = client.get("/api/generate")
    assert response.status_code == 200
    assert "id: micropad_controller" in response.json["automation_yaml"]
    assert response.json["filename"] == "micropad_controller.yaml"
    assert set(response.json["mqtt"]) == {
        "micropad/pages/all",
        "micropad/page/current",
        "micropad/keymap",
    }


@pytest.mark.parametrize(
    "path",
    ["/api/config", "/api/validate", "/api/generate", "/api/load-current-page"],
)
def test_api_errors_never_echo_saved_token(client, store: ConfigStore, path: str) -> None:
    config = store.load().model_copy(deep=True)
    config.settings.ha_token = "sentinel-secret-token"  # noqa: S105
    store.save(config)
    if path == "/api/config":
        response = client.post(path, data="not-json", content_type="application/json")
    else:
        response = client.get(path + "?page_id=missing")
    assert "sentinel-secret-token" not in response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/config", "put"),
        ("/api/meta", "post"),
        ("/api/generate", "post"),
        ("/healthz", "post"),
    ],
)
def test_wrong_methods_use_json_405(client, path: str, method: str) -> None:
    response = getattr(client, method)(path)
    assert response.status_code == 405
    assert response.json["error"]["code"] == "method_not_allowed"


def test_unknown_api_route_uses_json_404(client) -> None:
    response = client.get("/api/not-a-route")
    assert response.status_code == 404
    assert response.json["error"]["code"] == "not_found"


def test_validate_accepts_valid_payload_without_persisting(client, store: ConfigStore) -> None:
    payload = client.get("/api/config").json
    payload["pages"][0]["title"] = "Validate-Only"
    before = store.load().pages[0].title
    response = client.post("/api/validate", json=payload)
    assert response.status_code == 200
    assert response.json["ok"] is True
    assert response.json["config"]["pages"][0]["title"] == "Validate-Only"
    # Validate must not persist the candidate.
    assert store.load().pages[0].title == before


def test_validate_rejects_bad_payload_with_json_422(client) -> None:
    payload = client.get("/api/config").json
    payload["pages"][0]["title"] = ""
    response = client.post("/api/validate", json=payload)
    assert response.status_code == 422
    assert response.json["error"]["code"] == "validation_error"


def test_load_current_page_returns_matching_payload_and_unknown_page_is_422(client) -> None:
    response = client.get("/api/load-current-page")
    assert response.status_code == 200
    assert response.json["page_id"] == "home"
    missing = client.get("/api/load-current-page?page_id=missing")
    assert missing.status_code == 422
    assert missing.json["error"]["code"] == "generation_error"


def test_malformed_json_returns_json_400(client) -> None:
    response = client.post("/api/config", data="not-json", content_type="application/json")
    assert response.status_code == 400
    assert response.json["error"]["code"] == "bad_request"


def test_download_api_returns_yaml_attachment(client) -> None:
    response = client.get("/api/download/api")
    assert response.status_code == 200
    assert response.mimetype == "application/yaml"
    assert (
        "attachment; filename=micropad_controller.yaml" in response.headers["Content-Disposition"]
    )
    assert "id: micropad_controller" in response.get_data(as_text=True)


def test_config_get_redacts_secrets(client, store: ConfigStore) -> None:
    config = store.load().model_copy(deep=True)
    config.settings.ha_token = "secret-token"  # noqa: S105
    config.settings.ssh_key = "secret-key"
    store.save(config)
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json["settings"]["ha_token"] == ""
    assert response.json["settings"]["ssh_key"] == ""
    assert response.json["settings"]["ha_token_configured"] is True
    assert response.json["settings"]["ssh_key_configured"] is True


def test_saving_stale_ui_config_preserves_server_entity_cache(client, store) -> None:
    """P1.11: an old UI config (empty/stale entity_cache) must not wipe the cache
    the server filled via /api/ha/entities."""
    config = store.load().model_copy(deep=True)
    config.entity_cache = [
        EntitySummary(
            entity_id="light.desk", friendly_name="Desk", domain="light", state="on"
        )
    ]
    store.save(config)

    stale = client.get("/api/config").json
    stale["entity_cache"] = []
    stale["pages"][0]["title"] = "Changed"
    response = client.post("/api/config", json=stale)
    assert response.status_code == 200
    assert store.load().pages[0].title == "Changed"
    assert [c.entity_id for c in store.load().entity_cache] == ["light.desk"]


# --- Home Assistant and SSH action/upload routes (Task 12) -------------------


def entity_from_state(state: dict[str, object]) -> EntitySummary:
    entity_id = str(state["entity_id"])
    attributes = dict(state.get("attributes", {}))
    return EntitySummary(
        entity_id=entity_id,
        friendly_name=str(attributes.get("friendly_name", entity_id)),
        domain=entity_id.partition(".")[0],
        state=str(state.get("state", "unknown")),
        unit=str(attributes.get("unit_of_measurement", "")),
        minimum=attributes.get("min"),
        maximum=attributes.get("max"),
        step=attributes.get("step"),
    )


class RecordingHA:
    def __init__(
        self,
        failure: Exception | None = None,
        settings: object | None = None,
    ) -> None:
        self.failure = failure
        self.settings = settings or SimpleNamespace(ha_token="test-token")  # noqa: S106
        self.generated = None
        self.reads = 0
        self.states: list[dict[str, object]] = []

    @property
    def read_back_count(self) -> int:
        return max(0, self.reads - 1)

    def test_auth(self):
        if isinstance(self.failure, HAClientError):
            raise self.failure
        return {"message": "API running."}

    def get_automation(self, automation_id):
        self.reads += 1
        if self.reads == 2 and isinstance(self.failure, HADeploymentError):
            raise self.failure
        return None if self.reads == 1 else self.generated

    def put_automation(self, automation_id, automation):
        self.generated = automation

    def call_service(self, domain, service, data):
        return []

    def publish_mqtt(self, topic, payload, retain=True):
        return None

    def list_states(self):
        return self.states

    def list_entities(self):
        if isinstance(self.failure, HAClientError):
            raise self.failure
        return [entity_from_state(state) for state in self.states]


class RecordingSSH:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.yaml_text = ""

    def deploy(self, yaml_text: str) -> str:
        if self.failure is not None:
            raise self.failure
        self.yaml_text = yaml_text
        return "/config/automations/.micropad.yaml.test"


@pytest.fixture
def action_harness(store):
    ha = RecordingHA()
    ssh = RecordingSSH()
    app = create_app(
        store.path,
        ha_client_factory=lambda settings: ha,
        ssh_deployer_factory=lambda settings: ssh,
    )
    return app.test_client(), ha, ssh


def test_ha_test_reports_connection_success(action_harness) -> None:
    client, _, _ = action_harness
    response = client.post("/api/ha/test")
    assert response.status_code == 200
    assert response.json == {"ok": True, "message": "API running."}


def test_upload_api_returns_success_only_after_deployer_result(action_harness) -> None:
    client, recording_ha, _ = action_harness
    response = client.post("/api/upload/api")
    assert response.status_code == 200
    assert response.json == {
        "ok": True,
        "created": True,
        "automation_id": "micropad_controller",
        "published_topics": ["micropad/pages/all", "micropad/page/current", "micropad/keymap"],
        "verified": False,
        "unverified_topics": ["micropad/pages/all", "micropad/page/current", "micropad/keymap"],
        "message": (
            "Automation read-back verified; retained MQTT topics were published but "
            "NOT read back: micropad/pages/all, micropad/page/current, micropad/keymap"
        ),
    }
    assert recording_ha.read_back_count == 1


def test_upload_api_reports_verified_only_with_a_passing_mqtt_verifier(store) -> None:
    """P1.14: 'verified' is claimed only after the retained topics were read back."""
    ha = RecordingHA()
    app = create_app(
        store.path,
        ha_client_factory=lambda settings: ha,
        mqtt_verifier_factory=lambda settings: (lambda expected: []),
    )
    response = app.test_client().post("/api/upload/api")
    assert response.status_code == 200
    assert response.json["verified"] is True
    assert response.json["unverified_topics"] == []
    assert response.json["message"] == (
        "Home Assistant upload verified (automation read-back + retained topics)."
    )


def test_upload_api_partial_failure_reports_exact_remaining_changes(store) -> None:
    """P1.14: a verifier mismatch is a 502 partial failure listing what remains."""

    class PartialHA(RecordingHA):
        def delete_automation(self, automation_id):  # pragma: no cover - exercised via rollback
            return None

    ha = PartialHA()
    app = create_app(
        store.path,
        ha_client_factory=lambda settings: ha,
        mqtt_verifier_factory=lambda settings: (lambda expected: ["micropad/keymap"]),
    )
    response = app.test_client().post("/api/upload/api")
    assert response.status_code == 502
    assert response.json["error"]["code"] == "ha_partial_deployment"
    assert response.json["error"]["details"] == ["micropad/keymap"]


def test_ha_entities_normalizes_and_persists_cache(action_harness, store) -> None:
    client, recording_ha, _ = action_harness
    recording_ha.states = [
        {
            "entity_id": "number.level",
            "state": "12",
            "attributes": {
                "friendly_name": "Level",
                "min": 0,
                "max": 20,
                "step": 2,
                "unit_of_measurement": "%",
            },
        }
    ]
    response = client.get("/api/ha/entities")
    assert response.status_code == 200
    assert response.json[0]["domain"] == "number"
    assert store.load().entity_cache[0].friendly_name == "Level"


def test_ssh_download_is_shell_and_upload_uses_generated_yaml(action_harness) -> None:
    client, _, recording_ssh = action_harness
    download = client.get("/api/download/ssh")
    assert download.status_code == 200
    assert download.mimetype == "text/x-shellscript"
    assert download.get_data(as_text=True).startswith("#!/usr/bin/env bash")
    upload = client.post("/api/upload/ssh")
    assert upload.status_code == 200
    assert upload.json["remote_path"] == "/config/automations/micropad.yaml"
    assert upload.json["temporary_path"] == "/config/automations/.micropad.yaml.test"
    assert recording_ssh.yaml_text.startswith("# AI-assisted development")


def test_ssh_download_never_leaks_secrets(store) -> None:
    config = store.load().model_copy(deep=True)
    config.settings.ha_token = "sentinel-ha-token"  # noqa: S105
    config.settings.ssh_key = "sentinel-ssh-key"
    store.save(config)
    app = create_app(store.path, ssh_deployer_factory=lambda s: RecordingSSH())
    response = app.test_client().get("/api/download/ssh")
    body = response.get_data(as_text=True)
    assert "sentinel-ha-token" not in body
    assert "sentinel-ssh-key" not in body
    assert "sentinel" not in response.headers.get("Content-Disposition", "")


def test_upload_ssh_reports_concrete_remote_and_temporary_paths(action_harness) -> None:
    client, _, _ = action_harness
    response = client.post("/api/upload/ssh")
    assert response.status_code == 200
    assert response.json["ok"] is True
    assert response.json["message"] == "SSH upload verified."
    assert set(response.json) == {"ok", "remote_path", "temporary_path", "message"}


@pytest.mark.parametrize(
    ("path", "method", "failure", "status", "code"),
    [
        ("/api/ha/test", "post", HAClientError("authentication", 401), 502, "ha_error"),
        ("/api/ha/entities", "get", HAClientError("state listing", 500), 502, "ha_error"),
        ("/api/upload/api", "post", HADeploymentError("mismatch"), 502, "ha_verification_error"),
        ("/api/upload/ssh", "post", SSHUploadError("reload failed"), 502, "ssh_error"),
    ],
)
def test_adapter_failures_use_redacted_json_contract(
    store, path: str, method: str, failure: Exception, status: int, code: str
) -> None:
    ha = RecordingHA(failure if isinstance(failure, (HAClientError, HADeploymentError)) else None)
    ssh = RecordingSSH(failure if isinstance(failure, SSHUploadError) else None)
    app = create_app(
        store.path,
        ha_client_factory=lambda settings: ha,
        ssh_deployer_factory=lambda settings: ssh,
    )
    response = getattr(app.test_client(), method)(path)
    assert response.status_code == status
    assert response.json["error"]["code"] == code
    assert set(response.json["error"]) == {"code", "message", "details"}


def test_empty_ha_token_fails_deployment_with_verification_error(store) -> None:
    """The deferred empty-ha_token guard must surface as a clear failure."""
    ha = RecordingHA(settings=SimpleNamespace(ha_token=""))
    app = create_app(store.path, ha_client_factory=lambda settings: ha)
    response = app.test_client().post("/api/upload/api")
    assert response.status_code == 502
    assert response.json["error"]["code"] == "ha_verification_error"


def test_missing_ssh_settings_fail_clearly(store) -> None:
    """Real SSHDeployer rejects empty ssh_host/ssh_user/ssh_key via the route."""
    app = create_app(store.path)
    response = app.test_client().post("/api/upload/ssh")
    assert response.status_code == 502
    assert response.json["error"]["code"] == "ssh_error"


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/ha/test", "get"),
        ("/api/ha/entities", "post"),
        ("/api/upload/api", "get"),
        ("/api/upload/ssh", "get"),
    ],
)
def test_action_routes_wrong_methods_use_json_405(client, path: str, method: str) -> None:
    response = getattr(client, method)(path)
    assert response.status_code == 405
    assert response.json["error"]["code"] == "method_not_allowed"
