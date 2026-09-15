# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Browser bootstrap: the shell loads contracts and selects the home page."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect


@pytest.mark.browser
def test_loads_contracts_and_selects_home(page, app_url):
    page.goto(app_url)
    expect(page.get_by_role("heading", name="MicroPad Configurator")).to_be_visible()
    expect(page.locator('[data-page-id="home"]')).to_have_attribute("aria-current", "page")
    expect(page.locator("#save-status")).to_have_text("Saved")


@pytest.mark.browser
def test_page_commands_persist_complete_config(page, app_url, captured_requests, config_writes):
    page.goto(app_url)
    page.get_by_role("button", name="Add child page").click()
    page.get_by_label("Page title").fill("Office")
    page.get_by_role("button", name="Rename page").click()
    page.get_by_role("button", name="Duplicate page").click()
    expect(page.get_by_role("treeitem", name="Office copy")).to_be_visible()
    page.get_by_role("button", name="Delete page").click()
    page.get_by_role("button", name="Delete Office copy").click()
    expect(page.get_by_role("treeitem", name="Office copy")).to_have_count(0)
    expect(page.locator("#save-status")).to_have_text("Saved")
    assert config_writes()[-1]["path"] == "/api/config"
    assert config_writes()[-1]["json"]["pages"][0]["page_id"] == "home"


@pytest.mark.browser
def test_tree_arrow_keys_change_selection(page, app_url):
    page.goto(app_url)
    home = page.get_by_role("treeitem", name="Home")
    home.focus()
    home.press("ArrowDown")
    expect(page.locator('[aria-current="page"]')).not_to_have_attribute("data-page-id", "home")


@pytest.mark.browser
def test_drag_reorders_siblings_and_reparents_inside(page, app_url, captured_requests, config_writes):
    page.goto(app_url)
    page.locator('[data-page-id="kitchen"]').drag_to(page.locator('[data-page-node-id="living-room"] [data-drop-position="before"]'))
    page.locator('[data-page-id="upstairs"]').drag_to(page.locator('[data-page-node-id="living-room"] [data-drop-position="inside"]'))
    expect(page.locator("#save-status")).to_have_text("Saved")
    saved = config_writes()[-1]["json"]
    ids = [entry["page_id"] for entry in saved["pages"]]
    assert ids.index("kitchen") < ids.index("living-room")
    assert next(entry for entry in saved["pages"] if entry["page_id"] == "upstairs")["parent"] == "living-room"


def test_drag_rejects_descendant_cycle_and_home_move(page, app_url, captured_requests, config_writes):
    page.goto(app_url)
    before = len(config_writes())
    page.locator('[data-page-id="living-room"]').drag_to(page.locator('[data-page-node-id="upstairs"] [data-drop-position="inside"]'))
    expect(page.locator("#app-status")).to_contain_text("cannot be moved")
    page.locator('[data-page-id="home"]').drag_to(page.locator('[data-page-node-id="kitchen"] [data-drop-position="inside"]'))
    expect(page.locator("#app-status")).to_contain_text("Home cannot be moved")
    assert len(config_writes()) == before