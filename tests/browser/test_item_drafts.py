# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Real Flask + Chromium tests for the frontend contract fixes.

* P1.8: new items are UI drafts — they reach the persisted config only when
  every required field is set; incomplete drafts show concrete field errors and
  leave the saved config unchanged.
* P1.10: free-text entities typed in the keymap editor persist via input/change/
  blur without entity discovery; invalid syntax stays local with an inline hint
  and never reaches the server.
* P1.12: after a failed save the DOM re-renders from the last confirmed server
  state and the structured backend error is displayed.
"""

from __future__ import annotations

from pathlib import Path
from threading import Thread

import pytest
import requests
from playwright.sync_api import expect
from werkzeug.serving import make_server

from micropad.app import create_app
from micropad.config_store import ConfigStore
from micropad.models import default_config


@pytest.fixture
def app_url(tmp_path: Path):
    example = tmp_path / "config.example.json"
    example.write_text(default_config().model_dump_json(by_alias=True), encoding="utf-8")
    store = ConfigStore(tmp_path / "config.json", example)
    app = create_app(store.path)
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _server_config(base: str) -> dict:
    return requests.get(base + "/api/config", timeout=5).json()


def _open_editor(page, base: str) -> None:
    page.goto(base + "/")
    page.wait_for_selector("#page-tree", timeout=8000)


# --- P1.8 drafts -------------------------------------------------------------


def test_incomplete_draft_shows_field_errors_and_keeps_saved_config(page, app_url: str) -> None:
    _open_editor(page, app_url)
    page.click("button:has-text('Add item')")
    draft = page.locator("fieldset.item-draft")
    expect(draft).to_be_visible()
    expect(draft.locator("button:has-text('Save item')")).to_be_disabled()
    text = draft.inner_text()
    assert "Name is required." in text
    assert 'Entity is required for type "sensor".' in text

    draft.locator("input[aria-label='Draft name']").fill("Half-finished")
    # Still missing the entity: errors remain, nothing persists, save disabled.
    expect(draft.locator("button:has-text('Save item')")).to_be_disabled()
    assert "Entity is required" in draft.inner_text()

    items = _server_config(app_url)["pages"][0]["items"]
    assert items == []


def test_complete_draft_persists_and_survives_reload(page, app_url: str) -> None:
    _open_editor(page, app_url)
    page.click("button:has-text('Add item')")
    draft = page.locator("fieldset.item-draft")
    draft.locator("input[aria-label='Draft name']").fill("Desk 名")
    draft.locator("select[aria-label='Draft type']").select_option("light")
    draft.locator("input[aria-label='Draft entity ID']").fill("light.custom")
    expect(draft.locator("button:has-text('Save item')")).to_be_enabled()
    draft.locator("button:has-text('Save item')").click()

    expect(page.locator("fieldset[data-item-index='0']")).to_be_visible()

    items = _server_config(app_url)["pages"][0]["items"]
    assert items and items[0]["name"] == "Desk 名"
    assert items[0]["entity"] == "light.custom"
    assert items[0]["type"] == "light"

    page.goto(app_url + "/")
    page.wait_for_selector("#page-tree", timeout=8000)
    expect(page.locator("fieldset[data-item-index='0'] input[aria-label='Name']")).to_have_value("Desk 名", timeout=8000)


# --- P1.10 keymap free-text entity -------------------------------------------


def test_keymap_free_text_entity_persists_without_discovery(page, app_url: str) -> None:
    _open_editor(page, app_url)

    # r0c0 default action is 'home'; switch it to a Device action with an entity.
    page.click("button:has-text('Toggle')")
    entity_input = page.locator("input[aria-label='Entity ID for binding']")
    expect(entity_input).to_be_visible()
    entity_input.fill("light.custom")
    entity_input.blur()
    # Wait until the async save has been confirmed by the server before reading
    # it back (the keymap DOM does not change on a value-only save).
    expect(page.locator("#save-status")).to_have_text("Saved", timeout=8000)

    binding = _server_config(app_url)["global_keymap"]["r0c0"]
    assert binding["action"] == "toggle"
    assert binding["entity"] == "light.custom"


def test_keymap_invalid_entity_stays_local_and_never_reaches_server(
    page, app_url: str
) -> None:
    _open_editor(page, app_url)

    page.click("button:has-text('Toggle')")
    entity_input = page.locator("input[aria-label='Entity ID for binding']")
    entity_input.fill("Light.Desk")
    entity_input.blur()

    expect(page.locator("#keymap-editor")).to_contain_text("Invalid entity id")
    binding = _server_config(app_url)["global_keymap"]["r0c0"]
    # The stuck value is a UI-local draft, not persisted state.
    assert binding["entity"] == ""
    assert "Light.Desk" not in str(_server_config(app_url))


# --- P1.12 rollback to confirmed server state ---------------------------------


def test_failed_save_rolls_back_dom_and_shows_structured_error(page, app_url: str) -> None:
    _open_editor(page, app_url)

    # First create a real item through the draft flow (P1.8), then break its
    # name: the backend rejects it with a structured 422 and the DOM must
    # re-render from the last confirmed server state (P1.12).
    page.click("button:has-text('Add item')")
    draft = page.locator("fieldset.item-draft")
    draft.locator("input[aria-label='Draft name']").fill("Original")
    draft.locator("select[aria-label='Draft type']").select_option("light")
    draft.locator("input[aria-label='Draft entity ID']").fill("light.custom")
    draft.locator("button:has-text('Save item')").click()
    expect(page.locator("fieldset[data-item-index='0']")).to_be_visible(timeout=8000)
    # Wait for the async save to be confirmed before mutating the item.
    expect(page.locator("#save-status")).to_have_text("Saved", timeout=8000)

    name_input = page.locator("fieldset[data-item-index='0'] input[aria-label='Name']")
    original = name_input.input_value()
    name_input.fill("")
    name_input.blur()

    expect(name_input).to_have_value(original, timeout=8000)
    expect(page.locator("#app-status")).to_contain_text("name must not be empty", timeout=8000)
    # The saved config is unchanged.
    assert _server_config(app_url)["pages"][0]["items"][0]["name"] == original