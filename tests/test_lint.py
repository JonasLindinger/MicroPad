# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Every finding the lint can raise about a row control or a key gesture.

The lint is the layer that explains a draft the pad would refuse (``error``) or
silently ignore (``warning``), and the row controls plus the gesture bindings added a
whole vocabulary of ways to get that wrong. A finding nobody knows the trigger of is
a finding that does not help, so each one is pinned here with the configuration that
produces it *and* a well-formed counterpart that produces nothing.
"""

from __future__ import annotations

import pytest

from micropad.lint import analyze
from tests.factories import item, page

KEY_IDS = [
    "r0c0", "r0c1", "r0c2", "r0c3",
    "r1c0", "r1c1", "r1c2", "r1c3",
    "r2c0", "r2c1", "r2c2", "r2c3",
    "enc_up", "enc_down",
]


def _binding(action: str = "none", entity: str = "", target_page: str = "") -> dict[str, object]:
    return {"action": action, "entity": entity, "target_page": target_page}


def _keymap() -> dict[str, dict[str, object]]:
    return {key_id: _binding() for key_id in KEY_IDS}


def _config(items: list[dict[str, object]], keymap: dict | None = None) -> dict[str, object]:
    """A minimal config document: one page, an explicit keymap (the lint needs no model)."""
    config: dict[str, object] = {
        "settings": {},
        "pages": [page(items=items)],
        "global_keymap": keymap if keymap is not None else _keymap(),
    }
    return config


def _codes(config: dict[str, object]) -> list[str]:
    return [finding.code for finding in analyze(config).findings]


def _findings(config: dict[str, object], code: str) -> list:
    return [finding for finding in analyze(config).findings if finding.code == code]


# --- row controls ------------------------------------------------------------


def test_unknown_control_is_an_error():
    config = _config([item(name="Lamp", item_type="light", entity="light.a", control="dimmer")])
    findings = _findings(config, "unknown_control")
    assert [finding.severity for finding in findings] == ["error"]
    assert "dimmer" in findings[0].message
    assert findings[0].where == "pages[0].items[0].control"


def test_slider_on_a_type_without_an_adjustable_value_is_an_error():
    config = _config([item(name="Fan", item_type="switch", entity="switch.fan", control="slider")])
    findings = _findings(config, "slider_without_adjustable_value")
    assert [finding.severity for finding in findings] == ["error"]
    assert "switch" in findings[0].message


@pytest.mark.parametrize(
    ("field", "value", "code", "where"),
    [
        # min == max: no room to step, and the finding points at the range itself
        ("max_value", 0.0, "slider_without_range", "pages[0].items[0].min"),
        ("step", 0.0, "slider_without_step", "pages[0].items[0].step"),
    ],
)
def test_a_slider_without_a_usable_range_or_step_is_an_error(field, value, code, where):
    kwargs = {
        "name": "Lamp",
        "item_type": "light",
        "entity": "light.a",
        "control": "slider",
        "min_value": 0.0,
        "max_value": 100.0,
        "step": 5.0,
    }
    kwargs[field] = value
    findings = _findings(_config([item(**kwargs)]), code)
    assert [finding.severity for finding in findings] == ["error"]
    assert findings[0].where == where


def test_a_well_formed_slider_produces_no_control_finding():
    config = _config([
        item(name="Lamp", item_type="light", entity="light.a", control="slider", step=5.0),
        item(name="Fan", item_type="switch", entity="switch.fan"),
    ])
    assert [
        code for code in _codes(config)
        if code in {
            "unknown_control",
            "slider_without_adjustable_value",
            "slider_without_range",
            "slider_without_step",
        }
    ] == []


# --- gestures and directional actions ---------------------------------------


def test_adjust_on_a_matrix_key_is_a_warning():
    keymap = _keymap()
    keymap["r0c0"] = _binding("adjust")
    findings = _findings(_config([], keymap), "directional_action_on_a_key")
    assert [finding.severity for finding in findings] == ["warning"]
    assert findings[0].where == "global_keymap.r0c0"
    assert "enc_up/enc_down" in findings[0].message


def test_adjust_on_the_encoder_is_not_reported():
    keymap = _keymap()
    keymap["enc_up"] = _binding("adjust")
    keymap["enc_down"] = _binding("adjust")
    assert _codes(_config([], keymap)) == [] or "directional_action_on_a_key" not in _codes(
        _config([], keymap)
    )


def test_a_gesture_on_the_encoder_is_a_warning():
    keymap = _keymap()
    keymap["enc_up"] = {
        **_binding("toggle", "light.a"),
        "hold": _binding("off", "light.a"),
    }
    findings = _findings(_config([], keymap), "gesture_on_the_encoder")
    assert [finding.severity for finding in findings] == ["warning"]
    assert findings[0].where == "global_keymap.enc_up.hold"


def test_a_directional_action_inside_a_gesture_is_a_warning():
    keymap = _keymap()
    keymap["r0c1"] = {**_binding(), "hold": _binding("adjust", "light.a")}
    findings = _findings(_config([], keymap), "directional_action_on_a_gesture")
    assert [finding.severity for finding in findings] == ["warning"]
    assert findings[0].where == "global_keymap.r0c1.hold"


def test_a_gesture_with_a_target_but_no_action_is_a_warning():
    keymap = _keymap()
    keymap["r0c1"] = {**_binding(), "double": _binding("none", "light.a")}
    findings = _findings(_config([], keymap), "gesture_without_action")
    assert [finding.severity for finding in findings] == ["warning"]
    assert findings[0].where == "global_keymap.r0c1.double"


def test_a_gesture_action_without_its_entity_is_a_warning():
    keymap = _keymap()
    keymap["r0c1"] = {**_binding(), "hold": _binding("toggle")}
    findings = _findings(_config([], keymap), "gesture_without_entity")
    assert [finding.severity for finding in findings] == ["warning"]
    assert findings[0].where == "global_keymap.r0c1.hold"


def test_a_fully_bound_gesture_produces_no_keymap_finding():
    keymap = _keymap()
    keymap["r0c1"] = {
        **_binding("toggle", "light.a"),
        "hold": _binding("off", "light.a"),
        "double": _binding("navigate", target_page="home"),
    }
    assert [
        code for code in _codes(_config([], keymap))
        if code in {
            "directional_action_on_a_key",
            "gesture_on_the_encoder",
            "directional_action_on_a_gesture",
            "gesture_without_action",
            "gesture_without_entity",
        }
    ] == []
