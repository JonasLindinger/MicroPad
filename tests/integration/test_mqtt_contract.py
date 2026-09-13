# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Cross-component MQTT contract tests for the MicroPad release integration.

These establish the versioned wire contract as the single binding point for
firmware, backend, and frontend: exact topics/actions/key IDs plus strict
Draft 2020-12 JSON Schemas that every published payload must satisfy, and the
shared fixture factories consumed by later integration tasks.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
KEY_IDS = [f"r{row}c{column}" for row in range(3) for column in range(4)] + ["enc_up", "enc_down"]
ACTIONS = [
    "none",
    "enter",
    "back",
    "home",
    "settings",
    "scroll",
    "scroll_up",
    "scroll_down",
    "navigate",
    "keymap",
    "get_all_pages",
    "toggle",
    "on",
    "off",
    "press",
    "volume_up",
    "volume_down",
    "media_next",
    "media_prev",
    "edit",
    "confirm",
]
ITEM_TYPES = [
    "category",
    "light",
    "switch",
    "script",
    "button",
    "scene",
    "sensor",
    "media_player",
    "number",
    "settings",
    "back",
]
EXPECTED_TOPICS = {
    "event": {"name": "micropad/event", "retain": False},
    "pages": {"name": "micropad/pages/all", "retain": True},
    "current_page": {"name": "micropad/page/current", "retain": True},
    "keymap": {"name": "micropad/keymap", "retain": True},
    "power": {"name": "micropad/power", "retain": True},
}


def read_json(name: str) -> dict:
    return json.loads((ROOT / "contracts" / name).read_text(encoding="utf-8"))


# --- Step 1: contract shape (exact topics, actions, key IDs, version) ---


def test_contract_has_exact_topics_actions_and_keys():
    contract = read_json("mqtt-contract.json")
    assert contract["version"] == 1
    assert contract["topics"] == EXPECTED_TOPICS
    assert contract["actions"] == ACTIONS
    assert contract["key_ids"] == KEY_IDS
    assert contract["item_types"] == ITEM_TYPES
    assert contract["schemas"] == {
        "event": "event.schema.json",
        "page": "page.schema.json",
        "catalog": "catalog.schema.json",
        "keymap": "keymap.schema.json",
    }


def test_contract_preserves_payload_ceiling_facts():
    # The ship set carries three payload ceilings: 1800 bytes per page, 16000
    # bytes for the full catalog, and the keymap plugin ceiling (16380) that
    # stays inside the firmware's 16384-byte MQTT buffer.
    contract = read_json("mqtt-contract.json")
    assert contract["limits"]["page_bytes"] == 1800
    assert contract["limits"]["catalog_bytes"] == 16000
    assert contract["limits"]["keymap_bytes"] == 16380
    assert contract["limits"]["mqtt_buffer_bytes"] == 16384


def test_example_event_is_valid_and_unknown_fields_are_rejected():
    schema = read_json("event.schema.json")
    valid = {
        "action": "toggle",
        "entity": "light.example",
        "value": 1,
        "page_id": "home",
        "target_page": "",
    }
    jsonschema.validate(valid, schema)
    invalid = valid | {"service": "shell_command.run"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, schema)


# --- Step 4: strict Draft 2020-12 schema enforcement ---


@pytest.mark.parametrize(
    "name",
    [
        "event.schema.json",
        "page.schema.json",
        "catalog.schema.json",
        "keymap.schema.json",
    ],
)
def test_every_contract_schema_is_valid_draft_2020_12(name):
    Draft202012Validator.check_schema(read_json(name))


def _item(
    name="Stock",
    item_type="sensor",
    entity="sensor.stock",
    state="",
    value=0.0,
    min_value=0.0,
    max_value=100.0,
    step=1.0,
    unit="",
    editable=False,
    target_page="",
):
    from tests.factories import item

    return item(
        name,
        item_type,
        entity,
        state,
        value,
        min_value,
        max_value,
        step,
        unit,
        editable,
        target_page,
    )


@pytest.fixture
def page_schema():
    return read_json("page.schema.json")


def test_empty_page_is_valid(page_schema):
    jsonschema.validate(
        {"page_id": "home", "title": "Home", "parent": "", "items": []},
        page_schema,
    )


def test_category_navigation_item_valid(page_schema):
    page = {
        "page_id": "lights",
        "title": "Lights",
        "parent": "home",
        "items": [_item("Bedroom", "category", "", "", 0.0, 0.0, 100.0, 1.0, "", False, "bedroom")],
    }
    jsonschema.validate(page, page_schema)


def test_bounded_number_item_valid(page_schema):
    page = {
        "page_id": "office",
        "title": "Office",
        "parent": "home",
        "items": [_item("AC", "number", "number.ac", "", 21.0, 16.0, 30.0, 0.5, "°C", True, "")],
    }
    jsonschema.validate(page, page_schema)


def test_page_rejects_unknown_item_field(page_schema):
    page = {
        "page_id": "home",
        "title": "Home",
        "parent": "",
        "items": [{**_item(), "service": "light.turn_on"}],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(page, page_schema)


def test_page_rejects_empty_page_id(page_schema):
    page = {"page_id": "", "title": "Home", "parent": "", "items": []}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(page, page_schema)


def test_page_rejects_unknown_item_type(page_schema):
    page = {"page_id": "home", "title": "Home", "parent": "", "items": [_item(item_type="group")]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(page, page_schema)


def test_page_rejects_step_of_zero(page_schema):
    page = {
        "page_id": "office",
        "title": "Office",
        "parent": "",
        "items": [_item("AC", "number", "number.ac", "", 21.0, 0.0, 30.0, 0.0, "", True, "")],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(page, page_schema)


def test_catalog_valid_and_requires_pages_envelope():
    from tests.factories import catalog, page

    schema = read_json("catalog.schema.json")
    jsonschema.validate(
        catalog([page("home", "Home"), page("bedroom", "Bedroom", "home")]),
        schema,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"items": []}, schema)
    empty = catalog([])
    empty["stray"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(empty, schema)


def test_keymap_requires_all_fourteen_keys_and_canonical_order():
    from tests.factories import keymap

    schema = read_json("keymap.schema.json")
    build = keymap()
    jsonschema.validate(build, schema)
    assert list(build) == KEY_IDS

    del build[KEY_IDS[0]]  # missing key -> incomplete keymap rejected
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(build, schema)


def test_keymap_rejects_fifteenth_key():
    from tests.factories import keymap

    schema = read_json("keymap.schema.json")
    build = keymap()
    build["extra"] = {"action": "none", "entity": "", "target_page": ""}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(build, schema)


def test_keymap_rejects_unknown_action():
    from tests.factories import keymap

    schema = read_json("keymap.schema.json")
    build = keymap()
    build["r0c0"] = {"action": "shutdown", "entity": "", "target_page": ""}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(build, schema)


# --- Step 4: shared factories build realistic fixtures ---


def test_factory_fixtures_validate_against_their_schemas():
    from tests.factories import catalog, event, keymap, page

    jsonschema.validate(page("home", "Home"), read_json("page.schema.json"))
    jsonschema.validate(catalog([page("home", "Home")]), read_json("catalog.schema.json"))
    jsonschema.validate(
        event("toggle", "light.example", 1, "home", ""), read_json("event.schema.json")
    )
    jsonschema.validate(event("navigate"), read_json("event.schema.json"))
    jsonschema.validate(keymap(), read_json("keymap.schema.json"))


def test_valid_config_is_a_full_typed_config():
    from micropad.models import AppConfig
    from tests.factories import valid_config

    config = valid_config()
    assert isinstance(config, AppConfig)
    assert {page.page_id for page in config.pages} == {"home", "bedroom", "office"}


def test_backend_generated_payloads_conform_to_the_contract():
    from micropad.generator import (
        generate_catalog_payload,
        generate_keymap_payload,
        generate_page_payload,
    )
    from tests.factories import valid_config

    config = valid_config()
    page_schema = read_json("page.schema.json")
    keymap_schema = read_json("keymap.schema.json")
    for page in config.pages:
        jsonschema.validate(generate_page_payload(config, page.page_id), page_schema)
        jsonschema.validate(
            json.loads(generate_keymap_payload(config, page.page_id)),
            keymap_schema,
        )
    jsonschema.validate(
        json.loads(generate_catalog_payload(config)), read_json("catalog.schema.json")
    )


def test_load_contract_returns_the_canonical_contract():
    from micropad.generator import load_contract

    contract = load_contract()
    assert contract["version"] == 1
    assert contract["topics"] == EXPECTED_TOPICS
    assert contract["actions"] == ACTIONS
    assert contract["key_ids"] == KEY_IDS


# --- Integration Task 2: all four consumers bind to the one contract ---


def test_generator_and_api_metadata_match_contract():
    from micropad.app import create_app
    from micropad.generator import generate_bundle
    from tests.factories import valid_config

    contract = read_json("mqtt-contract.json")
    bundle = generate_bundle(valid_config())
    payloads = {
        "pages": json.loads(bundle.catalog_payload),
        "current_page": json.loads(bundle.home_payload),
        "keymap": json.loads(bundle.home_keymap_payload),
    }
    assert tuple(payloads) == ("pages", "current_page", "keymap")
    app = create_app()
    body = app.test_client().get("/api/meta").get_json()
    assert body["mqtt_contract_version"] == contract["version"]
    assert body["key_ids"] == contract["key_ids"]
    assert {item["id"] for item in body["actions"]} == set(contract["actions"])


def test_generated_bundle_validates_against_every_schema():
    from micropad.generator import generate_bundle
    from tests.factories import valid_config

    bundle = generate_bundle(valid_config())
    jsonschema.validate(json.loads(bundle.catalog_payload), read_json("catalog.schema.json"))
    jsonschema.validate(json.loads(bundle.home_payload), read_json("page.schema.json"))
    jsonschema.validate(json.loads(bundle.home_keymap_payload), read_json("keymap.schema.json"))
