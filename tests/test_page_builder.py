# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Page generation from the Home Assistant entity cache (B5).

The builder must produce pages that the pad can actually store: item types that
match the entity domains, names inside the firmware's field caps, no more items than
a page holds, and no more pages than the pad keeps. These tests pin all four, plus
the two routes, because a generator that quietly produces an unpublishable page is
worse than no generator at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from micropad.constants import MAX_ITEMS_PER_PAGE, MAX_PAGES
from micropad.models import EntitySummary, parse_config
from micropad.page_builder import build_pages, group_counts

REPO_ROOT = Path(__file__).resolve().parents[1]


def _entity(entity_id: str, name: str, domain: str | None = None, **extra: object) -> dict:
    return {
        "entity_id": entity_id,
        "friendly_name": name,
        "domain": domain or entity_id.partition(".")[0],
        "state": extra.pop("state", "on"),
        **extra,
    }


def test_group_counts_marks_pageable_domains_and_ranks_them() -> None:
    cache = [
        _entity("light.kitchen", "Kitchen"),
        _entity("light.desk", "Desk"),
        _entity("binary_sensor.door", "Front door"),
        _entity("automation.one", "One"),
        _entity("automation.two", "Two"),
    ]
    groups = group_counts(cache)
    by_domain = {row["domain"]: row for row in groups}
    assert by_domain["light"]["count"] == 2
    assert by_domain["light"]["item_type"] == "light"
    assert by_domain["light"]["pageable"] is True
    # binary_sensor is pageable *as a sensor item*: the contract says so.
    assert by_domain["binary_sensor"]["item_type"] == "sensor"
    assert by_domain["automation"]["pageable"] is False
    # Pageable domains come first, then by count, so the UI's buttons are useful.
    assert [row["domain"] for row in groups][:3] == ["light", "binary_sensor", "automation"]


def test_build_pages_groups_by_domain_and_keeps_item_types_honest() -> None:
    cache = [
        _entity("light.kitchen", "Kitchen", state="on"),
        _entity("number.bedside", "Bedside", minimum=0, maximum=255, step=5, state="40"),
        _entity("scene.evening", "Evening", state="unknown"),
        _entity("automation.one", "Ignored"),
    ]
    generated = build_pages(cache)
    pages = {page["page_id"]: page for page in generated.pages}
    assert set(pages) == {"light", "number", "scene"}
    assert pages["light"]["title"] == "Light"
    assert pages["light"]["items"][0]["type"] == "light"
    assert pages["light"]["items"][0]["entity"] == "light.kitchen"
    assert pages["light"]["items"][0]["state"] == "on"
    # A number item is editable and carries the entity's real range.
    number = pages["number"]["items"][0]
    assert number["type"] == "number"
    assert number["editable"] is True
    assert (number["min"], number["max"], number["step"]) == (0, 255, 5)
    # A domain without an item type is reported, never silently dropped.
    assert generated.skipped == {"automation": 1}
    assert any("automation" in note for note in generated.notes)
    # Every generated page must be publishable as-is: the real model validates the
    # generated pages against a real configuration document.
    config = json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "golden_config.json").read_text(encoding="utf-8")
    )
    # Appended, not substituted: the model requires exactly one home page, and the
    # UI merges drafts the same way.
    config["pages"] = config["pages"] + generated.pages
    parse_config(config)  # raises ValidationError if a generated page is malformed


def test_build_pages_splits_when_a_domain_overflows_a_page() -> None:
    cache = [_entity(f"light.l{index:02d}", f"Light {index}") for index in range(25)]
    generated = build_pages(cache)
    assert [(page["page_id"], len(page["items"])) for page in generated.pages] == [
        ("light", MAX_ITEMS_PER_PAGE),
        ("light-2", 5),
    ]
    assert generated.pages[1]["title"] == "Light 2"


def test_build_pages_clips_to_the_field_caps_and_says_so() -> None:
    cache = [_entity("light." + "a" * 40, "N" * 60, state="S" * 60, unit="U" * 40)]
    generated = build_pages(cache)
    item = generated.pages[0]["items"][0]
    assert len(item["name"]) == 32
    assert len(item["state"]) == 32
    assert len(item["unit"]) == 16
    assert sum(1 for note in generated.notes if "shortened" in note) == 3


def test_build_pages_avoids_existing_ids_and_respects_the_page_ceiling() -> None:
    cache = [_entity(f"light.l{index:02d}", f"Light {index}") for index in range(3)]
    generated = build_pages(cache, existing_page_ids=["light"], existing_page_count=1)
    assert generated.pages[0]["page_id"] == "light-2"

    # Only two pages of room left, and the request would need three.
    many = [_entity(f"light.l{index:02d}", f"Light {index}") for index in range(60)]
    generated = build_pages(many, existing_page_count=MAX_PAGES - 2)
    assert len(generated.pages) == 2
    assert any("ceiling" in note for note in generated.notes)


def test_entity_groups_route_reports_the_cache(client) -> None:
    response = client.get("/api/entity-groups")
    assert response.status_code == 200
    body = response.get_json()
    assert "groups" in body and "limits" in body
    assert body["limits"]["max_pages"] == MAX_PAGES


def test_build_pages_route_returns_drafts_without_saving(client, store) -> None:
    store.save(
        store.load().model_copy(
            update={
                "entity_cache": [
                    EntitySummary(
                        entity_id="light.kitchen",
                        friendly_name="Kitchen",
                        domain="light",
                        state="on",
                    )
                ]
            }
        )
    )
    before = client.get("/api/config").get_json()["pages"]
    response = client.post("/api/build-pages", json={"domains": ["light"]})
    assert response.status_code == 200
    body = response.get_json()
    assert [page["page_id"] for page in body["pages"]] == ["light"]
    assert body["pages"][0]["items"][0]["entity"] == "light.kitchen"
    # Drafts only: the stored configuration is untouched until the UI saves it.
    assert client.get("/api/config").get_json()["pages"] == before


def test_build_pages_route_rejects_a_non_list_domains_field(client) -> None:
    response = client.post("/api/build-pages", json={"domains": "light"})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "bad_request"
