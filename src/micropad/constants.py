# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Shared public protocol constants."""

AI_NOTICE = (
    "AI-assisted development — firmware, HA automation, config generator and docs were "
    "created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without "
    "warranty; verify on your own hardware, don't use for safety-critical applications."
)
KEY_IDS = tuple(
    [f"r{row}c{column}" for row in range(3) for column in range(4)]
    + ["enc_up", "enc_down"]
)
ACTIONS = frozenset({
    "none", "enter", "back", "home", "settings", "scroll", "scroll_up",
    "scroll_down", "navigate", "keymap", "get_all_pages", "toggle", "on", "off",
    "press", "volume_up", "volume_down", "media_next", "media_prev", "edit", "confirm",
})
ITEM_TYPES = frozenset({
    "category", "light", "switch", "script", "button", "scene", "sensor",
    "media_player", "number", "settings", "back",
})
AUTOMATION_ID = "micropad_controller"
AUTOMATION_ALIAS = "MicroPad Controller"
EVENT_TOPIC = "micropad/event"
CATALOG_TOPIC = "micropad/pages/all"
CURRENT_PAGE_TOPIC = "micropad/page/current"
KEYMAP_TOPIC = "micropad/keymap"
POWER_TOPIC = "micropad/power"
MAX_ITEMS_PER_PAGE = 20
MAX_PAGE_PAYLOAD_BYTES = 8192
MAX_CATALOG_PAYLOAD_BYTES = 16000
MAX_MQTT_PAYLOAD_BYTES = 16380
# P1.9: dynamic Home Assistant state/unit strings are truncated to these UTF-8
# byte budgets before JSON generation so a rendered page can never blow the
# firmware's page buffer through a huge `states(...)` value. 128 bytes is well
# beyond what one e-paper row can display and keeps 20 items far under 8192.
MAX_REFLECTED_STATE_BYTES = 128
MAX_REFLECTED_UNIT_BYTES = 8
