# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Cross-component MQTT contract tests for the MicroPad release integration.

These establish the versioned wire contract as the single binding point for
firmware, backend, and frontend: exact topics/actions/key IDs plus strict
Draft 2020-12 JSON Schemas that every published payload must satisfy, and the
shared fixture factories consumed by later integration tasks.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
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
    "cover",
    "fan",
    "input_boolean",
    "lock",
]
EXPECTED_TOPICS = {
    "event": {"name": "micropad/event", "retain": False},
    "pages": {"name": "micropad/pages/all", "retain": True},
    "current_page": {"name": "micropad/page/current", "retain": True},
    "device": {"name": "micropad/device", "retain": True},
    "diag": {"name": "micropad/diag", "retain": True},
    "keymap": {"name": "micropad/keymap", "retain": True},
    "power": {"name": "micropad/power", "retain": True},
}


def read_json(name: str) -> dict:
    return json.loads((ROOT / "contracts" / name).read_text(encoding="utf-8"))


# --- Step 1: contract shape (exact topics, actions, key IDs, version) ---


def test_contract_has_exact_topics_actions_and_keys():
    contract = read_json("mqtt-contract.json")
    # Pinned deliberately: a revision bump is a conscious edit that also has to teach
    # scripts/generate_contract.py (see the guard test below).
    assert contract["version"] == 2
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
    # The ship set carries three payload ceilings: 8192 bytes per page, 16000
    # bytes for the full catalog, and the keymap plugin ceiling (16380) that
    # stays inside the firmware's 16384-byte MQTT buffer.
    contract = read_json("mqtt-contract.json")
    assert contract["limits"]["page_bytes"] == 8192
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
    # Pinned deliberately: a revision bump is a conscious edit that also has to teach
    # scripts/generate_contract.py (see the guard test below).
    assert contract["version"] == 2
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


# --- Contract single-source gates -------------------------------------------------
# Every consumer keeps a copy of some contract value: the firmware header (generated),
# the browser bundle (generated), the sketch's enum-bounded tables, the backend's
# constants and the model's item rules. These tests assert each copy still agrees with
# contracts/mqtt-contract.json, so a contract edit cannot leave one side behind.


def _snake_case(name: str) -> str:
    """``GetAllPages`` -> ``get_all_pages``; ``EncUp`` -> ``enc_up``."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", "_", name).lower()


def _enum_members(source: str, enum: str) -> list[str]:
    body = re.search(rf"enum class {enum} : uint8_t \{{(.*?)\}};", source, re.S)
    assert body is not None, f"enum class {enum} not found in the firmware core"
    return [member.strip() for member in body.group(1).replace("\n", " ").split(",") if member.strip()]


def _core_constants() -> dict[str, int]:
    header = (ROOT / "firmware" / "micropad_core.h").read_text(encoding="utf-8")
    return {name: int(value) for name, value in re.findall(r"constexpr size_t (\w+) = (\d+);", header)}


def test_generated_contract_sources_are_in_sync():
    """firmware/protocol_contract.h and static/js/contracts.js are generated files."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_contract.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_codegen_knows_the_shipped_contract_revision():
    """The codegen refuses a contract revision it has not been taught.

    That refusal is deliberate: a revision bump has to be a conscious edit, not a number
    change that quietly regenerates the old shape. This keeps the taught set and the
    shipped contract from drifting apart in the other direction - a bump that updates the
    JSON but not the codegen would fail here instead of in a release build.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import generate_contract

    contract = read_json("mqtt-contract.json")
    assert contract["version"] in generate_contract.SUPPORTED_CONTRACT_VERSIONS, (
        "contracts/mqtt-contract.json was bumped without teaching scripts/generate_contract.py"
    )


def test_constants_derive_from_the_contract():
    from micropad import constants

    contract = read_json("mqtt-contract.json")
    assert list(constants.KEY_IDS) == contract["key_ids"]
    assert constants.ACTIONS == set(contract["actions"])
    assert constants.ITEM_TYPES == set(contract["item_types"])
    assert constants.ACTION_META == tuple(contract["action_meta"])
    assert constants.ITEM_TYPE_META == tuple(contract["item_type_meta"])
    assert constants.TOPICS == {key: value["name"] for key, value in contract["topics"].items()}
    assert constants.TOPIC_RETAINS == {
        key: bool(value["retain"]) for key, value in contract["topics"].items()
    }
    assert constants.LIMITS == contract["limits"]
    assert constants.FIELD_CAPS == contract["caps"]["field_caps"]


def test_firmware_enum_order_matches_the_contract():
    """The firmware indexes its name tables by enum value, so enum order is the wire order."""
    core = (ROOT / "firmware" / "micropad_core.h").read_text(encoding="utf-8")
    contract = read_json("mqtt-contract.json")
    assert [_snake_case(name) for name in _enum_members(core, "Action")] == contract["actions"]
    assert [_snake_case(name) for name in _enum_members(core, "ItemType")] == contract["item_types"]
    assert [_snake_case(name) for name in _enum_members(core, "KeyId")] == contract["key_ids"]


def test_firmware_caps_match_the_contract():
    """The caps the pad advertises on micropad/device are its real compile-time limits."""
    contract = read_json("mqtt-contract.json")
    caps = contract["caps"]
    fields = caps["field_caps"]
    const = _core_constants()
    assert const["PAGE_ID_CAP"] - 1 == fields["page_id"]
    assert const["TITLE_CAP"] - 1 == fields["title"]
    assert const["ITEM_NAME_CAP"] - 1 == fields["name"]
    assert const["ENTITY_CAP"] - 1 == fields["entity"]
    assert const["STATE_CAP"] - 1 == fields["state"]
    assert const["UNIT_CAP"] - 1 == fields["unit"]
    assert const["MAX_PAGES"] == caps["max_pages"]
    assert const["MAX_ITEMS_PER_PAGE"] == caps["max_items_per_page"]
    assert const["KEY_COUNT"] == caps["key_count"]
    assert const["MQTT_BUFFER_BYTES"] == caps["mqtt_buffer_bytes"]
    assert const["LOOP_TASK_STACK_BYTES"] == caps["loop_task_stack_bytes"]
    # Ceilings must fit the pad's receive buffer, else the MQTT client drops the
    # payload and the pad keeps its old page (the failure this contract exists to
    # prevent).
    assert contract["limits"]["mqtt_buffer_bytes"] == caps["mqtt_buffer_bytes"]
    assert contract["limits"]["catalog_bytes"] < caps["mqtt_buffer_bytes"]
    assert contract["limits"]["keymap_bytes"] < caps["mqtt_buffer_bytes"]
    assert contract["limits"]["page_bytes"] < caps["mqtt_buffer_bytes"]


def test_firmware_item_type_descriptors_match_the_contract():
    """micropad_core.cpp's table and the contract's item_type_meta are one truth in two files."""
    source = (ROOT / "firmware" / "micropad_core.cpp").read_text(encoding="utf-8")
    rows = re.findall(
        r"\{(ItemType::\w+), (Action::\w+), (Action::\w+), (true|false)\}", source
    )
    assert rows, "ITEM_TYPE_DESCRIPTORS not found in firmware/micropad_core.cpp"
    firmware = {
        _snake_case(item_type.split("::")[1]): (
            _snake_case(default_action.split("::")[1]),
            _snake_case(alternate.split("::")[1]),
            editable == "true",
        )
        for item_type, default_action, alternate, editable in rows
    }
    # default_action = a tap, alternate_action = a hold, editable = the confirm gesture
    contract = {
        entry["id"]: (entry["default_action"], entry["alternate_action"], entry["editable"])
        for entry in (read_json("mqtt-contract.json")["item_type_meta"])
    }
    assert firmware == contract


def test_backend_item_rules_follow_the_contract_descriptors():
    """models.py's entity/target-page rules are driven by the same descriptor rows."""
    from pydantic import ValidationError

    from micropad.models import PageItem

    for entry in read_json("mqtt-contract.json")["item_type_meta"]:
        item_type = entry["id"]
        kwargs = {"name": "probe", "type": item_type}
        if entry["needs_target_page"]:
            with pytest.raises(ValidationError):
                PageItem(**kwargs)
            PageItem(**kwargs, target_page="home")
        if entry["ha_domains"]:
            with pytest.raises(ValidationError):
                PageItem(**kwargs)
            PageItem(**kwargs, entity=f"{entry['ha_domains'][0]}.probe")
            wrong = "switch" if entry["ha_domains"][0] != "switch" else "light"
            with pytest.raises(ValidationError):
                PageItem(**kwargs, entity=f"{wrong}.probe")


def test_api_meta_exposes_caps_and_item_type_descriptors():
    """The configurator needs the device's real limits to warn before it publishes."""
    from micropad.app import create_app

    contract = read_json("mqtt-contract.json")
    body = create_app().test_client().get("/api/meta").get_json()
    assert body["limits"] == contract["limits"]
    assert body["item_type_meta"] == contract["item_type_meta"]
    assert body["caps"] == {
        "max_pages": contract["caps"]["max_pages"],
        "max_items_per_page": contract["caps"]["max_items_per_page"],
        "key_count": contract["caps"]["key_count"],
        "field_caps": contract["caps"]["field_caps"],
    }
