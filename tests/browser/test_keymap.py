# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

"""Browser keymap: all fourteen inputs render and bind, and grouped actions
with contextual targets persist through the store."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

# The action chips live in the binding panel. Since B4 the board's key faces also
# show each key's effective action, so a bare name match is ambiguous — address the
# panel explicitly.
PANEL = '.binding-panel'

KEY_IDS = [f"r{row}c{col}" for row in range(3) for col in range(4)] + ["enc_up", "enc_down"]


@pytest.mark.browser
def test_all_fourteen_inputs_are_unique_selectable_and_rebindable(page, app_url, captured_requests):
    page.goto(app_url)
    controls = page.locator("[data-key-id]")
    assert controls.count() == 14
    assert sorted(controls.evaluate_all("nodes => nodes.map(node => node.dataset.keyId)")) == sorted(KEY_IDS)
    for key_id in KEY_IDS:
        page.locator(f'[data-key-id="{key_id}"]').click()
        expect(page.locator(f'[data-key-id="{key_id}"]')).to_have_attribute("aria-pressed", "true")
        page.locator(PANEL).get_by_role("button", name="No action").click()
    expect(page.locator("#save-status")).to_have_text("Saved")
    saved = captured_requests[-1]["json"]["global_keymap"]
    assert len(saved) == 14
    assert all(saved[key_id]["action"] == "none" for key_id in KEY_IDS)


@pytest.mark.browser
def test_action_groups_show_only_contextual_target(page, app_url, captured_requests):
    page.goto(app_url)
    for group in ("Navigation", "Scrolling", "Device", "Media", "System"):
        expect(page.get_by_role("group", name=group)).to_be_visible()
    page.locator(PANEL).get_by_role("button", name="Toggle").click()
    entity = page.get_by_label("Entity ID for binding")
    expect(entity).to_be_visible()
    entity.fill("Desk Lamp")
    page.get_by_role("option", name="Desk Lamp — light.desk_lamp").click()
    page.locator(PANEL).get_by_role("button", name="Navigate").click()
    expect(entity).to_be_hidden()
    page.get_by_label("Target page for binding").select_option("living-room")
    expect(page.locator("#save-status")).to_have_text("Saved")
    binding = captured_requests[-1]["json"]["global_keymap"]["r0c0"]
    assert binding == {"action": "navigate", "entity": "", "target_page": "living-room"}


@pytest.mark.browser
def test_binding_hint_shows_until_required_argument_selected(page, app_url):
    page.goto(app_url)
    page.locator(PANEL).get_by_role("button", name="Toggle").click()
    entity = page.get_by_label("Entity ID for binding")
    expect(entity).to_be_visible()
    expect(page.get_by_text("Choose an entity.")).to_be_visible()
    entity.fill("Desk Lamp")
    page.get_by_role("option", name="Desk Lamp — light.desk_lamp").click()
    expect(page.get_by_text("Choose an entity.")).to_be_hidden()
    page.locator(PANEL).get_by_role("button", name="Navigate").click()
    expect(page.get_by_text("Choose a target page.")).to_be_visible()


@pytest.mark.browser
def test_entity_listbox_is_not_inside_the_label(page, app_url):
    # Accessibility (review I1): a combobox listbox is not phrasing content, so it
    # must be a SIBLING of the wrapping <label>, never a child of the label — that
    # avoids folding listbox text into the label name and breaking label activation.
    page.goto(app_url)
    page.locator(PANEL).get_by_role("button", name="Toggle").click()
    entity = page.get_by_label("Entity ID for binding")
    entity.fill("Desk Lamp")
    expect(page.get_by_role("listbox", name="Matching Home Assistant entities")).to_be_visible()
    dom = entity.evaluate("""el => {
        const label = el.closest('label');
        const lb = document.querySelector('ul[role="listbox"]');
        return { insideLabel: !!(label && label.contains(lb)), isSibling: !!(lb && lb.parentElement !== label) };
    }""")
    assert dom["insideLabel"] is False
    assert dom["isSibling"] is True


@pytest.mark.browser
def test_page_scope_shows_ancestor_source_and_clears_override(page, app_url, captured_requests):
    page.goto(app_url)
    page.get_by_label("Keymap scope").select_option("upstairs")
    page.locator('[data-key-id="r1c0"]').click()
    expect(page.get_by_text("Inherited from Living room")).to_be_visible()
    page.locator(PANEL).get_by_role("button", name="Toggle").click()
    page.get_by_label("Entity ID for binding").fill("light.upstairs")
    page.get_by_label("Entity ID for binding").press("Tab")
    expect(page.get_by_text("Override on Upstairs")).to_be_visible()
    page.get_by_role("button", name="Clear override").click()
    expect(page.get_by_text("Inherited from Living room")).to_be_visible()
    expect(page.locator("#save-status")).to_have_text("Saved")
    saved = captured_requests[-1]["json"]
    upstairs = next(page_data for page_data in saved["pages"] if page_data["page_id"] == "upstairs")
    assert "r1c0" not in upstairs.get("keymap", {})


@pytest.mark.browser
def test_page_override_writes_into_canonical_pages_keymap_and_validates(page, app_url, captured_requests):
    # The canonical backend shape stores per-page key overrides as pages[].keymap
    # (models.py Page.keymap), NOT a top-level page_overrides container. AppConfig is
    # StrictModel with extra="forbid", so a payload carrying a stray page_overrides key
    # is rejected. This test proves the frontend writes the canonical shape.
    from micropad.models import parse_config
    page.goto(app_url)
    page.get_by_label("Keymap scope").select_option("upstairs")
    page.locator('[data-key-id="r1c0"]').click()
    entity = page.get_by_label("Entity ID for binding")
    entity.fill("Desk Lamp")
    page.get_by_role("option", name="Desk Lamp — light.desk_lamp").click()
    expect(page.get_by_text("Override on Upstairs")).to_be_visible()
    expect(page.locator("#save-status")).to_have_text("Saved")
    payload = captured_requests[-1]["json"]
    assert "page_overrides" not in payload
    upstairs = next(page_data for page_data in payload["pages"] if page_data["page_id"] == "upstairs")
    assert upstairs.get("keymap", {}).get("r1c0") == {
        "action": "toggle", "entity": "light.desk_lamp", "target_page": ""
    }
    # parse_config is the real backend write path used by POST /api/config
    # (parse_config -> AppConfig.model_validate). It normalizes the global_keymap key
    # order the frontend emits (encoder keys serialize first, which the strict model
    # would otherwise flag) and fills any absent bindings, then validates the payload.
    # The exact body the fake captured must round-trip this parser.
    parse_config(payload)


@pytest.mark.browser
def test_restore_defaults_requires_confirmation_and_clears_all_overrides(page, app_url, captured_requests):
    # Restoring defaults must be a guarded, two-step action: opening the dialog alone
    # must not write anything, and cancel must not either. Confirming persists exactly
    # one write that restores the global keymap to the fourteen canonical defaults and
    # clears every page-local key override.
    from micropad.models import parse_config
    page.goto(app_url)
    before = len(captured_requests)
    page.get_by_role("button", name="Restore keymap defaults").click()
    expect(page.get_by_role("dialog", name="Restore keymap defaults")).to_be_visible()
    page.get_by_role("button", name="Cancel restore").click()
    assert len(captured_requests) == before
    page.get_by_role("button", name="Restore keymap defaults").click()
    page.get_by_role("button", name="Restore defaults now").click()
    expect(page.locator("#save-status")).to_have_text("Saved")
    saved = captured_requests[-1]["json"]
    # Canonical AppConfig shape: page-local key overrides live in pages[].keymap and the
    # backend is StrictModel with extra="forbid", so there is NO top-level page_overrides
    # field. Restore returns the global keymap to defaults and empties every page map.
    assert "page_overrides" not in saved
    assert {key_id: binding["action"] for key_id, binding in saved["global_keymap"].items()} == {
        "r0c0": "home", "r0c1": "none", "r0c2": "none", "r0c3": "enter",
        "r1c0": "none", "r1c1": "none", "r1c2": "none", "r1c3": "enter",
        "r2c0": "none", "r2c1": "none", "r2c2": "none", "r2c3": "back",
        "enc_up": "scroll_up", "enc_down": "scroll_down",
    }
    # Every page-local override is cleared (the fixture starts living-room with an r1c0
    # override) while the rest of the configuration is left intact.
    for page_data in saved["pages"]:
        assert not page_data.get("keymap")
    assert [page_data["page_id"] for page_data in saved["pages"]] == ["home", "living-room", "upstairs", "kitchen"]
    assert saved["entity_cache"][0]["entity_id"] == "light.desk_lamp"
    assert saved["settings"] == {}
    # The exact body must round-trip the real backend write path.
    parse_config(saved)


def test_scope_lists_global_and_every_page(page, app_url):
    page.goto(app_url)
    values = page.get_by_label("Keymap scope").locator("option").evaluate_all("nodes => nodes.map(node => node.value)")
    assert values == ["global", "home", "living-room", "upstairs", "kitchen"]

@pytest.mark.browser
def test_board_shows_each_keys_effective_action_and_origin(page, app_url):
    # B4: the board is the mental model. Every face carries the key id *and* the
    # action that key currently performs, so a page's keymap can be read at a glance
    # instead of clicking all fourteen keys; data-binding-origin says whether that
    # action is this page's override or inherited from global/ancestors.
    page.goto(app_url)
    faces = page.locator(".keypad button, .encoder button")
    assert faces.count() == 14
    expect(page.locator("[data-key-id='r0c0'] .key-action")).to_have_text("Home")
    expect(page.locator("[data-key-id='enc_up'] .key-action")).to_have_text("Scroll up")
    expect(page.locator("[data-key-id='enc_up'] .key-id")).to_have_text("ENC Left")
    # Global scope: everything is the shared default.
    expect(page.locator("[data-key-id='r0c0']")).to_have_attribute("data-binding-origin", "global")

    # Page scope: all keys inherit until one is overridden, and only that one turns
    # into an override.
    page.get_by_label("Keymap scope").select_option("home")
    expect(page.locator("[data-key-id='r0c0']")).to_have_attribute(
        "data-binding-origin", "inherited"
    )
    page.locator("[data-key-id='r1c0']").click()
    page.locator(PANEL).get_by_role("button", name="Toggle").click()
    expect(page.locator("[data-key-id='r1c0']")).to_have_attribute(
        "data-binding-origin", "override"
    )
    expect(page.locator("[data-key-id='r1c0'] .key-action")).to_have_text("Toggle")
    expect(page.locator("[data-key-id='r0c0']")).to_have_attribute(
        "data-binding-origin", "inherited"
    )
