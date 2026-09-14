# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Authenticated Home Assistant REST client with secret-redacting errors."""

from __future__ import annotations

from typing import Any, cast

import requests

from micropad.models import EntitySummary, Settings


class HAClientError(RuntimeError):
    """Raised for Home Assistant transport or HTTP errors without leaking secrets."""

    def __init__(self, operation: str, status_code: int | None = None) -> None:
        suffix = "" if status_code is None else f" (HTTP {status_code})"
        super().__init__(f"Home Assistant {operation} failed{suffix}")
        self.operation = operation
        self.status_code = status_code


class HomeAssistantClient:
    """Read and write Home Assistant resources through its long-lived-token REST API."""

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.base_url = settings.ha_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.ha_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, operation: str, **kwargs: Any) -> requests.Response:
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=self.headers,
                timeout=self.settings.request_timeout_seconds,
                verify=self.settings.verify_tls,
                **kwargs,
            )
        except requests.RequestException as error:
            raise HAClientError(operation) from error
        if response.status_code >= 400:
            raise HAClientError(operation, response.status_code)
        return response

    @staticmethod
    def _decode(response: requests.Response, operation: str) -> Any:
        try:
            return response.json()
        except ValueError as error:
            raise HAClientError(f"{operation} response decoding", response.status_code) from error

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return None

    def test_auth(self) -> dict[str, object]:
        response = self._request("GET", "/api/", "authentication")
        return dict(self._decode(response, "authentication"))

    def list_states(self) -> list[dict[str, object]]:
        response = self._request("GET", "/api/states", "state listing")
        return list(self._decode(response, "state listing"))

    def list_entities(self) -> list[EntitySummary]:
        entities: list[EntitySummary] = []
        for state in self.list_states():
            entity_id = str(state["entity_id"])
            attributes = cast(dict[str, object], state.get("attributes", {}))
            entities.append(
                EntitySummary(
                    entity_id=entity_id,
                    friendly_name=str(attributes.get("friendly_name", entity_id)),
                    domain=entity_id.partition(".")[0],
                    state=str(state.get("state", "unknown")),
                    unit=str(attributes.get("unit_of_measurement", "")),
                    minimum=self._optional_float(attributes.get("min")),
                    maximum=self._optional_float(attributes.get("max")),
                    step=self._optional_float(attributes.get("step")),
                )
            )
        return entities

    def get_automation(self, automation_id: str) -> dict[str, object] | None:
        path = f"/api/config/automation/config/{automation_id}"
        try:
            response = self._request("GET", path, "automation read-back")
            return dict(self._decode(response, "automation read-back"))
        except HAClientError as error:
            if error.status_code == 404:
                return None
            raise

    def put_automation(self, automation_id: str, automation: dict[str, object]) -> None:
        path = f"/api/config/automation/config/{automation_id}"
        self._request("POST", path, "automation write", json=automation)

    def delete_automation(self, automation_id: str) -> None:
        """Delete an automation (used to roll back a first-time create)."""
        path = f"/api/config/automation/config/{automation_id}"
        self._request("DELETE", path, "automation delete")

    def call_service(
        self, domain: str, service: str, data: dict[str, object]
    ) -> list[dict[str, object]]:
        response = self._request(
            "POST", f"/api/services/{domain}/{service}", f"{domain}.{service}", json=data
        )
        return list(self._decode(response, f"{domain}.{service}"))

    def publish_mqtt(self, topic: str, payload: str, retain: bool = True) -> None:
        self.call_service(
            "mqtt", "publish", {"topic": topic, "payload": payload, "retain": retain, "qos": 0}
        )
