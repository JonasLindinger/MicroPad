# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

"""Browser row controls and key gestures.

Two features the pad gained with contract revision 3, both configured here:

* a row's ``control`` — Button or Slider (an encoder turn adjusts a slider row
  while the cursor rests on it, no Enter press needed), and
* the two gestures a key can carry besides its tap — Hold and Double press,
  each with its own action, entity and target page.

The gestures are stored inside the binding (``hold`` / ``double``), so the panel
must not lose them when the tap action is re-picked, and the board must show at a
glance which keys carry one.

Selector note: ``get_by_label`` matches a label *substring*, case-insensitively, so
``get_by_label("On")`` also matches the "Control" field (``C-on-trol``) and
``get_by_label("State")``-style short labels are a trap. The state switch is
therefore always addressed with ``exact=True``.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

PANEL = ".binding-panel"


def _add_item(page, name: str, item_type: str, entity: str) -> None:
    """Create one saved item through the draft flow (the only add path)."""
    page.get_by_role("button", name="Add item").click()
    draft = page.locator("fieldset.item-draft")
    draft.get_by_label("Draft name").fill(name)
    draft.get_by_label("Draft type").select_option(item_type)
    if entity:
        draft.get_by_label("Draft entity ID").fill(entity)
    draft.locator("button:has-text('Save item')").click()
    expect(page.locator('[data-item-index="0"]')).to_be_visible()


@pytest.mark.browser
def test_control_select_turns_a_light_row_into_a_slider(page, app_url, config_writes):
    page.goto(app_url)
    _add_item(page, "Desk lamp", "light", "light.desk")

    card = page.locator('[data-item-index="0"]')
    control = card.get_by_label("Control")
    expect(control).to_be_enabled()
    assert control.locator("option").evaluate_all("nodes => nodes.map(node => node.value)") == [
        "button",
        "slider",
    ]
    control.select_option("slider")
    expect(page.locator("#save-status")).to_have_text("Saved")

    saved = config_writes()[-1]["json"]["pages"][0]["items"][0]
    assert saved["control"] == "slider"
    assert saved["type"] == "light"

    # Switching back is one selection away, and the row keeps its other fields.
    control.select_option("button")
    expect(page.locator("#save-status")).to_have_text("Saved")
    assert config_writes()[-1]["json"]["pages"][0]["items"][0]["control"] == "button"


@pytest.mark.browser
def test_control_select_offers_only_button_for_a_type_without_a_value(page, app_url):
    # A sensor has no adjustable value: offering a slider there would let the
    # operator build a row the pad can only scroll past.
    page.goto(app_url)
    _add_item(page, "Temperature", "sensor", "sensor.temp")

    control = page.locator('[data-item-index="0"]').get_by_label("Control")
    assert control.locator("option").evaluate_all("nodes => nodes.map(node => node.value)") == [
        "button"
    ]
    assert control.is_disabled()


@pytest.mark.browser
def test_item_state_checkbox_writes_the_boolean_state(page, app_url, config_writes):
    page.goto(app_url)
    _add_item(page, "Desk lamp", "light", "light.desk")

    card = page.locator('[data-item-index="0"]')
    # An empty state can become a boolean without overwriting anything.
    toggle = card.get_by_label("On", exact=True)
    expect(toggle).to_be_visible()
    expect(toggle).not_to_be_checked()
    toggle.check()
    expect(page.locator("#save-status")).to_have_text("Saved")
    assert config_writes()[-1]["json"]["pages"][0]["items"][0]["state"] == "on"

    # The text field follows the checkbox instead of showing a stale value.
    expect(card.get_by_label("State")).to_have_value("on")
    toggle.uncheck()
    expect(page.locator("#save-status")).to_have_text("Saved")
    assert config_writes()[-1]["json"]["pages"][0]["items"][0]["state"] == "off"


@pytest.mark.browser
def test_state_checkbox_is_absent_for_a_real_reading(page, app_url):
    # 23.4 is not a two-valued state: the editor must not offer a checkbox that
    # would silently turn a temperature into "on".
    page.goto(app_url)
    _add_item(page, "Temperature", "sensor", "sensor.temp")

    card = page.locator('[data-item-index="0"]')
    card.get_by_label("State").fill("23.4")
    expect(page.locator("#save-status")).to_have_text("Saved")
    # `to_have_count` counts DOM nodes, hidden or not: the switch stays in the card
    # (it is toggled in place) and must be *hidden* for a reading.
    expect(card.get_by_label("On", exact=True)).to_be_hidden()


@pytest.mark.browser
def test_hold_and_double_gestures_are_configured_and_kept(
    page, app_url, config_writes
):
    page.goto(app_url)
    page.locator('[data-key-id="r0c1"]').click()

    # The panel names the gesture with the timing the firmware enforces, taken
    # from /api/meta (contract caps), not from a hard-coded number.
    expect(page.get_by_text("Hold (600 ms)")).to_be_visible()
    expect(page.get_by_text("Double press (350 ms)")).to_be_visible()
    expect(page.locator('.gesture-block[data-gesture="hold"]')).to_contain_text("Not used")

    # Tap = toggle, hold = off on the same light: two different things on one key.
    page.locator(PANEL).get_by_role("button", name="Toggle").click()
    page.get_by_label("Entity ID for binding").fill("light.desk_lamp")
    page.get_by_label("Entity ID for binding").press("Tab")
    page.get_by_label("Hold action").select_option("off")
    page.get_by_label("Hold entity ID").fill("light.desk_lamp")
    page.get_by_label("Hold entity ID").press("Tab")
    expect(page.locator("#save-status")).to_have_text("Saved")

    binding = config_writes()[-1]["json"]["global_keymap"]["r0c1"]
    assert binding["action"] == "toggle"
    assert binding["entity"] == "light.desk_lamp"
    assert binding["hold"] == {
        "action": "off",
        "entity": "light.desk_lamp",
        "target_page": "",
    }
    assert binding["double"]["action"] == "none"

    # The board shows the gesture without clicking the key.
    expect(page.locator('[data-key-id="r0c1"] .key-gestures')).to_have_text("H")

    # Re-picking the tap action must not drop the gesture that was just bound.
    page.locator(PANEL).get_by_role("button", name="Press").click()
    expect(page.locator("#save-status")).to_have_text("Saved")
    binding = config_writes()[-1]["json"]["global_keymap"]["r0c1"]
    assert binding["action"] == "press"
    assert binding["hold"]["action"] == "off"

    # Clearing a gesture removes it completely, entity included.
    page.get_by_role("button", name="Clear hold").click()
    expect(page.locator("#save-status")).to_have_text("Saved")
    binding = config_writes()[-1]["json"]["global_keymap"]["r0c1"]
    assert binding["hold"] == {"action": "none", "entity": "", "target_page": ""}
    expect(page.locator('[data-key-id="r0c1"] .key-gestures')).to_have_count(0)


@pytest.mark.browser
def test_double_gesture_target_field_follows_the_chosen_action(
    page, app_url, config_writes
):
    page.goto(app_url)
    page.locator('[data-key-id="r2c1"]').click()

    # No target field while the gesture is unset.
    expect(page.get_by_label("Double press target page")).to_have_count(0)
    page.get_by_label("Double press action").select_option("navigate")
    expect(page.get_by_label("Double press target page")).to_be_visible()
    page.get_by_label("Double press target page").select_option("kitchen")
    expect(page.locator("#save-status")).to_have_text("Saved")

    binding = config_writes()[-1]["json"]["global_keymap"]["r2c1"]
    assert binding["double"] == {
        "action": "navigate",
        "entity": "",
        "target_page": "kitchen",
    }
    expect(page.locator('.gesture-block[data-gesture="double"]')).to_contain_text("Bound")
    expect(page.locator('[data-key-id="r2c1"] .key-gestures')).to_have_text("D")


@pytest.mark.browser
def test_gesture_blocks_stay_readable_on_a_tablet(page, app_url):
    # The keymap column is narrow at tablet width; a gesture block must not push
    # the page into horizontal scrolling and must keep the 44 px touch floor.
    page.set_viewport_size({"width": 768, "height": 1024})
    page.goto(app_url)
    page.locator('[data-key-id="r0c1"]').click()
    select = page.get_by_label("Hold action")
    expect(select).to_be_visible()
    assert select.bounding_box()["height"] >= 44
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
