# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Browser item editor: every schema field, removal, and numeric validation."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect


@pytest.mark.browser
def test_item_editor_persists_every_field(page, app_url, captured_requests):
    page.goto(app_url)
    page.get_by_role("button", name="Add item").click()
    card = page.locator('[data-item-index="0"]')
    card.get_by_label("Name").fill("Desk brightness")
    card.get_by_label("Type").select_option("number")
    card.get_by_label("Entity ID").fill("number.desk_brightness")
    card.get_by_label("State").fill("42")
    card.get_by_label("Value").fill("42")
    card.get_by_label("Minimum").fill("0")
    card.get_by_label("Maximum").fill("100")
    card.get_by_label("Step").fill("5")
    card.get_by_label("Unit").fill("%")
    card.get_by_label("Editable").check()
    card.get_by_label("Target page").select_option("living-room")
    card.get_by_label("Move item down").click()
    expect(page.locator("#save-status")).to_have_text("Saved")
    saved_item = captured_requests[-1]["json"]["pages"][0]["items"][1]
    assert saved_item == {"name":"Desk brightness","type":"number","entity":"number.desk_brightness","state":"42","value":42,"min":0,"max":100,"step":5,"unit":"%","editable":True,"target_page":"living-room"}


@pytest.mark.browser
def test_item_editor_removes_item(page, app_url, captured_requests):
    page.goto(app_url)
    page.get_by_role("button", name="Add item").click()
    page.locator('[data-item-index="0"]').get_by_label("Remove item").click()
    expect(page.locator("#save-status")).to_have_text("Saved")
    saved_items = captured_requests[-1]["json"]["pages"][0]["items"]
    assert len(saved_items) == 1


@pytest.mark.browser
def test_item_editor_rejects_non_finite_number(page, app_url, captured_requests):
    page.goto(app_url)
    page.get_by_role("button", name="Add item").click()
    card = page.locator('[data-item-index="0"]')
    card.get_by_label("Value").fill("42")
    expect(page.locator("#save-status")).to_have_text("Saved")
    card.get_by_label("Value").fill("Infinity")
    expect(page.locator("#app-status")).to_contain_text("Enter a finite number.")
    saved_item = captured_requests[-1]["json"]["pages"][0]["items"][0]
    assert saved_item["value"] == 42


@pytest.mark.browser
def test_template_cards_create_editable_child_page(page, app_url, captured_requests):
    page.goto(app_url)
    for label in ("Spotify", "Discord", "Lights", "Generic media"):
        expect(page.get_by_role("button", name=f"Create {label} page")).to_be_visible()
    page.get_by_role("button", name="Create Spotify page").click()
    expect(page.get_by_role("treeitem", name="Spotify")).to_have_attribute("aria-current", "page")
    expect(page.locator("#save-status")).to_have_text("Saved")
    saved = captured_requests[-1]["json"]
    spotify = next(entry for entry in saved["pages"] if entry["title"] == "Spotify")
    assert spotify["parent"] == "home"
    assert {item["name"] for item in spotify["items"]} >= {"Play/Pause", "Next", "Previous"}


@pytest.mark.browser
def test_item_editor_accepts_char_by_char_negative_decimal(page, app_url, captured_requests):
    # A keyboard user types a negative decimal character-by-character. The transient
    # first keystroke ("-") and mid-way prefixes ("-20.") are not finite, so the field
    # must neither hard-revert nor announce an error until the value forms a finite
    # number; the final "-20.5" is persisted.
    page.goto(app_url)
    page.get_by_role("button", name="Add item").click()
    card = page.locator('[data-item-index="0"]')
    value = card.get_by_label("Value")
    value.focus()
    page.keyboard.press("Control+A")
    page.keyboard.type("-20.5")
    expect(page.locator("#save-status")).to_have_text("Saved")
    expect(page.locator("#app-status")).not_to_contain_text("Enter a finite number.")
    card.get_by_label("Target page").focus()
    expect(page.locator("#save-status")).to_have_text("Saved")
    saved_item = captured_requests[-1]["json"]["pages"][0]["items"][0]
    assert saved_item["value"] == -20.5


@pytest.mark.browser
def test_entity_autocomplete_matches_friendly_name_and_writes_id(page, app_url, captured_requests):
    page.goto(app_url)
    input_ = page.locator('[data-item-index="0"]').get_by_label("Entity ID")
    input_.fill("Desk Lamp")
    option = page.get_by_role("option", name="Desk Lamp — light.desk_lamp")
    expect(option).to_be_visible()
    option.click()
    expect(input_).to_have_value("light.desk_lamp")
    expect(page.locator("#save-status")).to_have_text("Saved")
    assert captured_requests[-1]["json"]["pages"][0]["items"][0]["entity"] == "light.desk_lamp"


@pytest.mark.browser
def test_entity_autocomplete_keyboard_and_empty_result(page, app_url):
    page.goto(app_url)
    input_ = page.locator('[data-item-index="0"]').get_by_label("Entity ID")
    input_.fill("living")
    input_.press("ArrowDown")
    input_.press("Enter")
    expect(input_).to_have_value("light.living_room")
    input_.fill("entity that does not exist")
    expect(page.get_by_text("No matching Home Assistant entities.")).to_be_visible()
