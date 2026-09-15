# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Tests for the authenticated, redacting Home Assistant REST client."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import requests

from micropad.ha_client import HAClientError, HomeAssistantClient
from micropad.models import Settings


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    url: str
    headers: dict[str, str]
    timeout: float
    verify: bool
    json: dict[str, object] | None


class FakeResponse:
    def __init__(self, status_code: int, value: object, json_error: bool = False) -> None:
        self.status_code = status_code
        self.value = value
        self.json_error = json_error

    def json(self) -> object:
        if self.json_error:
            raise ValueError("malformed body")
        return self.value


class RecordingSession:
    def __init__(
        self,
        status_code: int = 200,
        response_json: object = None,
        network_error: Exception | None = None,
        json_error: bool = False,
    ) -> None:
        self.status_code = status_code
        self.response_json = {} if response_json is None else response_json
        self.network_error = network_error
        self.json_error = json_error
        self.requests: list[RecordedRequest] = []

    def request(self, method, url, headers, timeout, verify, json=None):  # type: ignore[no-untyped-def]
        self.requests.append(RecordedRequest(method, url, headers, timeout, verify, json))
        if self.network_error is not None:
            raise self.network_error
        return FakeResponse(self.status_code, self.response_json, self.json_error)


def _client(session: RecordingSession, **overrides: object) -> HomeAssistantClient:
    settings = Settings(
        ha_url=overrides.pop("ha_url", "http://ha"),
        ha_token=overrides.pop("ha_token", "token-value"),
        **overrides,  # type: ignore[arg-type]
    )
    return HomeAssistantClient(settings, session=session)


def test_get_automation_uses_bearer_auth_timeout_and_tls_verification() -> None:
    session = RecordingSession(response_json={"id": "micropad_controller"})
    client = HomeAssistantClient(
        Settings(ha_url="https://ha.example", ha_token="token-value"),  # noqa: S106
        session=session,
    )
    result = client.get_automation("micropad_controller")
    assert result == {"id": "micropad_controller"}
    request = session.requests[0]
    assert request.method == "GET"
    assert request.url == "https://ha.example/api/config/automation/config/micropad_controller"
    assert request.headers["Authorization"] == "Bearer token-value"
    assert request.verify is True
    assert request.timeout == 10.0


def test_missing_automation_returns_none_only_for_404() -> None:
    session = RecordingSession(status_code=404, response_json={"message": "not found"})
    client = HomeAssistantClient(Settings(ha_url="http://ha", ha_token="token"), session=session)  # noqa: S106
    assert client.get_automation("micropad_controller") is None


def test_verify_tls_false_disables_tls_verification() -> None:
    session = RecordingSession()
    client = _client(session, verify_tls=False)
    client.test_auth()
    assert session.requests[0].verify is False


def test_request_timeout_mapped_from_settings() -> None:
    session = RecordingSession()
    client = _client(session, request_timeout_seconds=3.5)
    client.test_auth()
    assert session.requests[0].timeout == 3.5


def test_authorization_token_present_on_every_request() -> None:
    session = RecordingSession()
    client = _client(session, ha_token="secret-token-1234")  # noqa: S106
    client.publish_mqtt("a/b", "x")
    assert session.requests[0].headers["Authorization"] == "Bearer secret-token-1234"


def test_test_auth_fetches_root_and_returns_message() -> None:
    session = RecordingSession(response_json={"message": "API running."})
    client = _client(session)
    result = client.test_auth()
    assert result == {"message": "API running."}
    assert session.requests[0].method == "GET"
    assert session.requests[0].url == "http://ha/api/"


def test_list_states_returns_decoded_state_list() -> None:
    session = RecordingSession(response_json=[{"entity_id": "light.a", "state": "on"}])
    client = _client(session)
    assert client.list_states() == [{"entity_id": "light.a", "state": "on"}]
    assert session.requests[0].url == "http://ha/api/states"


def test_list_entities_builds_typed_summaries() -> None:
    session = RecordingSession(
        response_json=[
            {
                "entity_id": "light.lamp",
                "state": "off",
                "attributes": {"friendly_name": "Lamp", "min": 0, "max": 100, "step": 5},
            },
            {
                "entity_id": "sensor.temp",
                "state": "21.5",
                "attributes": {"unit_of_measurement": "°C"},
            },
        ]
    )
    client = _client(session)
    entities = client.list_entities()
    assert [e.entity_id for e in entities] == ["light.lamp", "sensor.temp"]
    assert entities[0].domain == "light"
    assert entities[0].friendly_name == "Lamp"
    assert entities[0].state == "off"
    assert entities[0].maximum == 100
    assert entities[1].unit == "°C"
    assert entities[1].friendly_name == "sensor.temp"


def test_put_automation_posts_automation_json() -> None:
    automation = {"id": "micropad_controller", "alias": "MicroPad"}
    session = RecordingSession()
    client = _client(session, ha_url="https://ha")
    client.put_automation("micropad_controller", automation)
    request = session.requests[0]
    assert request.method == "POST"
    assert request.url == "https://ha/api/config/automation/config/micropad_controller"
    assert request.json == automation


def test_call_service_returns_response_list() -> None:
    session = RecordingSession(response_json=[{"success": True}])
    client = _client(session)
    result = client.call_service("light", "turn_on", {"entity_id": "light.a"})
    assert result == [{"success": True}]
    request = session.requests[0]
    assert request.method == "POST"
    assert request.url == "http://ha/api/services/light/turn_on"
    assert request.json == {"entity_id": "light.a"}


def test_publish_mqtt_uses_retain_and_qos_zero() -> None:
    session = RecordingSession(response_json=[])
    client = _client(session)
    client.publish_mqtt("home/micropad/key", "on", retain=True)
    request = session.requests[0]
    assert request.url == "http://ha/api/services/mqtt/publish"
    assert request.json == {"topic": "home/micropad/key", "payload": "on", "retain": True, "qos": 0}


def test_401_maps_to_authentication_error() -> None:
    session = RecordingSession(status_code=401, response_json={"message": "unauthorized"})
    client = _client(session)
    with pytest.raises(HAClientError) as captured:
        client.test_auth()
    assert captured.value.status_code == 401
    assert captured.value.operation == "authentication"


def test_403_maps_to_operation_error_with_status() -> None:
    session = RecordingSession(status_code=403, response_json={"message": "forbidden"})
    client = _client(session)
    with pytest.raises(HAClientError) as captured:
        client.list_states()
    assert captured.value.status_code == 403
    assert captured.value.operation == "state listing"


def test_500_maps_to_operation_error() -> None:
    session = RecordingSession(status_code=500, response_json={"message": "boom"})
    client = _client(session)
    with pytest.raises(HAClientError) as captured:
        client.list_states()
    assert captured.value.status_code == 500


def test_get_automation_re_raises_non_404_errors() -> None:
    session = RecordingSession(status_code=500, response_json={"message": "boom"})
    client = _client(session)
    with pytest.raises(HAClientError) as captured:
        client.get_automation("micropad_controller")
    assert captured.value.status_code == 500


def test_connection_error_wrapped_in_ha_client_error() -> None:
    session = RecordingSession(network_error=requests.ConnectionError("dropped"))
    client = _client(session)
    with pytest.raises(HAClientError) as captured:
        client.test_auth()
    assert captured.value.operation == "authentication"
    assert captured.value.status_code is None


def test_malformed_json_raises_ha_client_error() -> None:
    session = RecordingSession(response_json={}, json_error=True)
    client = _client(session)
    with pytest.raises(HAClientError) as captured:
        client.test_auth()
    assert captured.value.operation == "authentication response decoding"


def test_errors_never_include_token_or_response_body() -> None:
    session = RecordingSession(status_code=401, response_json={"message": "token-value invalid"})
    client = _client(session, ha_token="token-value")  # noqa: S106
    with pytest.raises(HAClientError) as captured:
        client.test_auth()
    assert str(captured.value) == "Home Assistant authentication failed (HTTP 401)"
    assert captured.value.operation == "authentication"
    assert captured.value.status_code == 401
    encoded = repr(captured.value)
    assert "token-value" not in encoded
    assert "invalid" not in encoded
