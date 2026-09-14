# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Backend/schema equality and byte-limit regression tests (P1.9, P1.13).

* Dynamic Home Assistant state strings are truncated to display-safe UTF-8 byte
  lengths before JSON generation, so a rendered page stays <= 8192 bytes, valid
  JSON, and schema-conformant even with 20k-character unicode states.
* PageItem.name must not be empty; numeric fields must be finite.
* Non-object top-level JSON at the API returns structured JSON 400/422, never an
  HTML 500.
* Real generator output validates against contracts/page.schema.json and
  contracts/keymap.schema.json.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import jsonschema
import pytest
from jsonschema import Draft202012Validator

from micropad.constants import MAX_PAGE_PAYLOAD_BYTES
from micropad.generator import (
    generate_bundle,
    generate_catalog_payload,
    generate_keymap_payload,
    generate_page_payload,
)
from micropad.models import EntitySummary, Page, PageItem, parse_config

ROOT = Path(__file__).resolve().parents[1]


def _schema(name: str) -> dict:
    return json.loads((ROOT / "contracts" / name).read_text(encoding="utf-8"))


def config_with_state_items(state: str) -> object:
    """A 20-item page where every entity carries the same enormous HA state."""
    items = [
        PageItem(
            name=f"Item {index} 名" if index % 2 == 0 else f"Item {index} – ümlaut",
            type="sensor",
            entity=f"sensor.sensor_{index}",
        )
        for index in range(20)
    ]
    states = [
        EntitySummary(
            entity_id=f"sensor.sensor_{index}",
            friendly_name=f"sensor {index}",
            domain="sensor",
            state=state,
            unit="μ",
        )
        for index in range(20)
    ]
    config = parse_config(
        {
            "settings": {"ha_url": "https://homeassistant.example:8123"},
            "pages": [{"page_id": "home", "title": "Home", "items": [i.model_dump(mode="json") for i in items]}],
            "entity_cache": [s.model_dump(mode="json") for s in states],
            "global_keymap": {},
        }
    )
    return config, states


# --- P1.9 dynamic state length limits ----------------------------------------


def test_huge_unicode_ha_states_are_truncated_within_page_limit() -> None:
    huge = ("ü" * 10000) + "😀" * 2500  # 20,000 codepoints, mixed UTF-8 lengths
    config, states = config_with_state_items(huge)
    payload = generate_page_payload(config, "home", states)
    text = json.dumps(payload, ensure_ascii=False)
    assert len(text.encode("utf-8")) <= MAX_PAGE_PAYLOAD_BYTES
    # Truncation never splits a UTF-8 codepoint.
    json.loads(text)


def test_huge_unicode_states_yield_valid_schema_conformant_page() -> None:
    huge = "状态" * 10000
    config, states = config_with_state_items(huge)
    payload = generate_page_payload(config, "home", states)
    Draft202012Validator(_schema("page.schema.json")).validate(payload)


def test_huge_unicode_states_catalog_within_contract_limit() -> None:
    huge = "€" * 20000
    config, states = config_with_state_items(huge)
    catalog = generate_catalog_payload(config, states)
    assert len(catalog.encode("utf-8")) <= 16000
    pages = json.loads(catalog)["pages"]
    for page in pages:
        Draft202012Validator(_schema("page.schema.json")).validate(page)


def test_normal_states_are_not_truncated() -> None:
    config, states = config_with_state_items("on")
    payload = generate_page_payload(config, "home", states)
    assert all(item["state"] == "on" for item in payload["items"])


# --- P1.13 name and finiteness ------------------------------------------------


def test_page_item_empty_name_is_rejected() -> None:
    with pytest.raises(ValueError):
        PageItem(name="", type="light", entity="light.desk")
    with pytest.raises(ValueError):
        PageItem(name="   ", type="light", entity="light.desk")


@pytest.mark.parametrize("field", ["value", "min", "max", "step"])
def test_non_finite_numbers_are_rejected(field: str) -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        kwargs = {"name": "X", "type": "number", "entity": "number.level", "step": 1}
        kwargs[field] = bad
        with pytest.raises(ValueError):
            PageItem(**kwargs)


def test_parse_config_rejects_nan_payload_via_api(client) -> None:
    payload = client.get("/api/config").json
    payload["pages"][0]["items"].append(
        {
            "name": "Bad",
            "type": "number",
            "entity": "number.level",
            "value": float("nan"),
            "min": 0,
            "max": 100,
            "step": 1,
            "unit": "",
            "editable": False,
            "state": "",
            "target_page": "",
        }
    )
    response = client.post("/api/config", json=payload)
    assert response.status_code == 422
    assert response.json["error"]["code"] == "validation_error"


@pytest.mark.parametrize("bad", [None, 3, "text", [1, 2]])
def test_non_object_top_level_json_is_json_400(client, bad) -> None:
    response = client.post("/api/config", json=bad)
    assert response.status_code == 400
    assert response.json["error"]["code"] == "bad_request"


def test_settings_as_number_is_json_422_not_html_500(client) -> None:
    payload = client.get("/api/config").json
    payload["settings"] = 1
    response = client.post("/api/config", json=payload)
    assert response.status_code in (400, 422)
    assert "error" in response.json


# --- P1.13 real generator output validates against the JSON schemas -------------


def test_generated_page_payload_validates_against_page_schema() -> None:
    config, _ = config_with_state_items("on")
    payload = generate_page_payload(config, "home")
    Draft202012Validator(_schema("page.schema.json")).validate(payload)


def test_generated_catalog_payload_validates_against_page_schema() -> None:
    config, _ = config_with_state_items("on")
    catalog = generate_catalog_payload(config)
    for page in json.loads(catalog)["pages"]:
        Draft202012Validator(_schema("page.schema.json")).validate(page)


def test_generated_keymap_payload_validates_against_keymap_schema() -> None:
    config, _ = config_with_state_items("on")
    payload = generate_keymap_payload(config, "home")
    Draft202012Validator(_schema("keymap.schema.json")).validate(json.loads(payload))


def test_bundle_payloads_validate_against_schemas() -> None:
    config, _ = config_with_state_items("on")
    bundle = generate_bundle(config)
    Draft202012Validator(_schema("page.schema.json")).validate(
        json.loads(bundle.home_payload)
    )
    Draft202012Validator(_schema("keymap.schema.json")).validate(
        json.loads(bundle.home_keymap_payload)
    )
    for page in json.loads(bundle.catalog_payload)["pages"]:
        Draft202012Validator(_schema("page.schema.json")).validate(page)