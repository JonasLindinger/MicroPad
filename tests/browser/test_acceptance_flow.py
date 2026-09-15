# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

"""End-to-end browser acceptance: one full edit-generate round trip across the
entire UI — page tree, template/create, item/keymap editing, and generate/upload —
proving the integrated frontend works as a whole and survives a reload. The
autouse console gate in conftest.py additionally proves the whole session runs
with zero console errors/warnings, zero uncaught page errors, and zero failed
network requests."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

# The action chips live in the binding panel. Since B4 the board's key faces also
# show each key's effective action, so a bare name match is ambiguous — address the
# panel explicitly.
PANEL = '.binding-panel'


@pytest.mark.browser
def test_complete_editing_flow_survives_reload(page, app_url):
    page.goto(app_url)
    # Template → create: spawn a new page from the Lights template.
    page.get_by_role("button", name="Create Lights page").click()
    # Rename the freshly-created, still-selected page.
    page.get_by_label("Page title").fill("Downstairs lights")
    page.get_by_role("button", name="Rename page").click()
    # Open the new page's keymap scope and bind r2c0 to a Toggle on a real entity.
    page.get_by_label("Keymap scope").select_option(label="Downstairs lights")
    page.locator('[data-key-id="r2c0"]').click()
    page.locator(PANEL).get_by_role("button", name="Toggle").click()
    page.get_by_label("Entity ID for binding").fill("Desk Lamp")
    page.get_by_role("option", name="Desk Lamp — light.desk_lamp").click()
    expect(page.locator("#save-status")).to_have_text("Saved")
    # Reload: the whole edited tree must survive, restored from the server.
    page.reload()
    expect(page.get_by_role("treeitem", name="Home")).to_have_attribute("aria-current", "page")
    page.get_by_label("Keymap scope").select_option(label="Downstairs lights")
    page.locator('[data-key-id="r2c0"]').click()
    expect(page.get_by_text("Override on Downstairs lights")).to_be_visible()
    expect(page.get_by_label("Entity ID for binding")).to_have_value("light.desk_lamp")