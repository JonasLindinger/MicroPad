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
    # Wait for each step's effect before the next one: on a slower machine the previous
    # action's save lands between the clicks, and asserting only at the end turns that into
    # a confusing "Office copy never appeared" instead of "the rename did not apply".
    expect(page.get_by_role("treeitem", name="New page")).to_be_visible()
    page.get_by_label("Page title").fill("Office")
    page.get_by_role("button", name="Rename page").click()
    expect(page.get_by_role("treeitem", name="Office")).to_be_visible()
    page.get_by_role("button", name="Duplicate page").click()
    expect(page.get_by_role("treeitem", name="Office copy")).to_be_visible()
    page.get_by_role("button", name="Delete page").click()
    page.get_by_role("button", name="Delete Office copy").click()
    expect(page.get_by_role("treeitem", name="Office copy")).to_have_count(0)
    expect(page.locator("#save-status")).to_have_text("Saved")
    assert config_writes()[-1]["path"] == "/api/config"
    assert config_writes()[-1]["json"]["pages"][0]["page_id"] == "home"


@pytest.mark.browser
def test_template_page_is_linked_from_its_parent(page, app_url, config_writes):
    """A created page must be *reachable* on the pad, not merely present in the config.

    The device builds every menu from the items of a page, so "Create Spotify page" used
    to produce a page that existed in the configurator and could never be opened on the
    board - exactly the reported symptom ("the website says there is a Spotify page, the
    board does not show it").
    """
    page.goto(app_url)
    page.get_by_role("button", name="Create Spotify page").click()
    expect(page.get_by_text("Spotify page created and linked from")).to_be_visible()
    expect(page.locator("#save-status")).to_have_text("Saved")
    config = config_writes()[-1]["json"]
    home = next(p for p in config["pages"] if p["page_id"] == "home")
    links = [item for item in home["items"] if item.get("target_page") == "spotify"]
    assert len(links) == 1
    assert links[0]["type"] == "category"
    assert links[0]["name"] == "Spotify"


@pytest.mark.browser
def test_rename_survives_a_re_render_between_typing_and_clicking(page, app_url):
    """The typed title must outlive a toolbar re-render.

    Every store change rebuilds the page toolbar, so the title field is a new element each
    render. The value used to live only in the DOM, and a re-render between typing and the
    rename click silently threw it away - the rename then did nothing while the click
    looked successful, and on a slow runner the following duplicate was named after the old
    title (this failed in CI and never locally). The re-render is provoked on purpose here
    by re-selecting the page, which dispatches select-page exactly like a save would.
    """
    page.goto(app_url)
    page.get_by_role("button", name="Add child page").click()
    expect(page.get_by_role("treeitem", name="New page")).to_be_visible()
    page.get_by_label("Page title").fill("Office")
    page.get_by_role("treeitem", name="New page").click()  # forces a re-render
    page.get_by_role("button", name="Rename page").click()
    expect(page.get_by_role("treeitem", name="Office")).to_be_visible()


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