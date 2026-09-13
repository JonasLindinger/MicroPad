# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Real-HTTP round-trip tests against a thread-backed fake Home Assistant."""

from __future__ import annotations

import json

from micropad.app import create_app
from micropad.models import PageItem
from tests.fakes.fake_ha import FakeHomeAssistant


def configure_store_for_fake(store, base_url: str) -> None:
    config = store.load().model_copy(deep=True)
    config.settings.ha_url = base_url
    config.settings.ha_token = "fake-token"  # noqa: S105 — synthetic placeholder credential
    store.save(config)


def test_fake_ha_create_round_trip_generates_writes_reloads_verifies_and_publishes(
    store,
) -> None:
    with FakeHomeAssistant() as fake:
        fake.states = [
            {
                "entity_id": "light.desk",
                "state": "on",
                "attributes": {"friendly_name": "Desk"},
            }
        ]
        config = store.load().model_copy(deep=True)
        config.settings.ha_url = fake.base_url
        config.settings.ha_token = "fake-token"  # noqa: S105 — synthetic placeholder credential
        config.pages[0].items = [PageItem(name="Desk", type="light", entity="light.desk")]
        store.save(config)
        app = create_app(store.path)
        response = app.test_client().post("/api/upload/api")
        assert response.status_code == 200
        assert response.json["created"] is True
        assert fake.automation["id"] == "micropad_controller"
        assert fake.automation_reads == 2
        assert fake.automation_writes == 1
        assert [(call.domain, call.service) for call in fake.service_calls] == [
            ("automation", "reload"),
            ("mqtt", "publish"),
            ("mqtt", "publish"),
            ("mqtt", "publish"),
        ]
        mqtt_calls = fake.service_calls[1:]
        assert [call.data["topic"] for call in mqtt_calls] == [
            "micropad/pages/all",
            "micropad/page/current",
            "micropad/keymap",
        ]
        assert all(call.data["retain"] is True for call in mqtt_calls)
        current_page = json.loads(mqtt_calls[1].data["payload"])
        assert current_page["page_id"] == "home"
        assert current_page["items"][0]["state"] == "on"
        assert len(json.loads(mqtt_calls[2].data["payload"])) == 14


def test_fake_ha_update_round_trip_reports_update(store) -> None:
    with FakeHomeAssistant() as fake:
        fake.automation = {"id": "micropad_controller"}
        configure_store_for_fake(store, fake.base_url)
        response = create_app(store.path).test_client().post("/api/upload/api")
        assert response.status_code == 200
        assert response.json["created"] is False
        assert fake.automation_writes == 1
        assert len([call for call in fake.service_calls if call.domain == "mqtt"]) == 3


def test_fake_ha_mismatched_read_back_returns_502_without_mqtt(store) -> None:
    with FakeHomeAssistant() as fake:
        fake.read_back_transform = lambda automation: {**automation, "alias": "Tampered"}
        configure_store_for_fake(store, fake.base_url)
        response = create_app(store.path).test_client().post("/api/upload/api")
        assert response.status_code == 502
        assert response.json["error"]["code"] == "ha_verification_error"
        assert not [call for call in fake.service_calls if call.domain == "mqtt"]
