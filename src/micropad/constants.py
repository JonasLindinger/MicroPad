# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Shared public protocol constants, derived from the canonical contract.

Every value below comes from ``contracts/mqtt-contract.json`` — the single source
of truth shared by the firmware (``firmware/protocol_contract.h``, which is
generated from it by ``scripts/generate_contract.py``), the config generator,
the ``/api/meta`` endpoint and the browser. Adding an action, an item type, a
topic or a limit is therefore a one-line contract change plus a regenerate;
nothing in this module is hand-maintained, so the four consumers cannot drift
apart.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

AI_NOTICE = (
    "AI-assisted development — firmware, HA automation, config generator and docs were "
    "created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without "
    "warranty; verify on your own hardware, don't use for safety-critical applications."
)

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "mqtt-contract.json"
SUPPORTED_CONTRACT_VERSION = 3


def load_contract() -> dict[str, Any]:
    """Load the canonical contract, failing closed on an unsupported version."""
    contract: dict[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if contract.get("version") != SUPPORTED_CONTRACT_VERSION:
        raise RuntimeError(f"unsupported MQTT contract version: {contract.get('version')!r}")
    return contract


CONTRACT = load_contract()
CONTRACT_VERSION: int = CONTRACT["version"]

# --- topics -----------------------------------------------------------------
TOPICS: dict[str, str] = {key: value["name"] for key, value in CONTRACT["topics"].items()}
TOPIC_RETAINS: dict[str, bool] = {
    key: bool(value["retain"]) for key, value in CONTRACT["topics"].items()
}
EVENT_TOPIC = TOPICS["event"]
CATALOG_TOPIC = TOPICS["pages"]
CURRENT_PAGE_TOPIC = TOPICS["current_page"]
KEYMAP_TOPIC = TOPICS["keymap"]
POWER_TOPIC = TOPICS["power"]
DEVICE_TOPIC = TOPICS["device"]

# --- protocol vocabulary ----------------------------------------------------
KEY_IDS = tuple(CONTRACT["key_ids"])
ACTIONS = frozenset(CONTRACT["actions"])
ITEM_TYPES = frozenset(CONTRACT["item_types"])
CONTROLS = tuple(CONTRACT["controls"])
GESTURES = tuple(CONTRACT["gestures"])
ACTION_META: tuple[dict[str, str], ...] = tuple(CONTRACT["action_meta"])
ITEM_TYPE_META: tuple[dict[str, Any], ...] = tuple(CONTRACT["item_type_meta"])
CONTROL_META: tuple[dict[str, str], ...] = tuple(CONTRACT["control_meta"])
GESTURE_META: tuple[dict[str, str], ...] = tuple(CONTRACT["gesture_meta"])

# --- device capabilities (mirrored by firmware/micropad_core.h) -------------
CAPS: dict[str, Any] = CONTRACT["caps"]
MAX_PAGES = int(CAPS["max_pages"])
MAX_ITEMS_PER_PAGE = int(CAPS["max_items_per_page"])

#: The one page every pad has: menus are built from items, so this is the only page a
#: device can reach without any other page linking to it.
HOME_PAGE_ID = "home"
KEY_COUNT = int(CAPS["key_count"])
QUEUE_CAPACITY = int(CAPS["queue_capacity"])
FIELD_CAPS: dict[str, int] = {key: int(value) for key, value in CAPS["field_caps"].items()}
#: Gesture timing the firmware enforces (contract caps): the configurator shows
#: these numbers so "hold" and "double press" mean something concrete.
HOLD_MS = int(CAPS["hold_ms"])
DOUBLE_PRESS_MS = int(CAPS["double_press_ms"])

# --- payload ceilings -------------------------------------------------------
LIMITS: dict[str, int] = {key: int(value) for key, value in CONTRACT["limits"].items()}
MAX_PAGE_PAYLOAD_BYTES = LIMITS["page_bytes"]
MAX_CATALOG_PAYLOAD_BYTES = LIMITS["catalog_bytes"]
MAX_MQTT_PAYLOAD_BYTES = LIMITS["keymap_bytes"]
MQTT_BUFFER_BYTES = LIMITS["mqtt_buffer_bytes"]

# Reflected Home Assistant state/unit strings are truncated to these UTF-8 byte
# budgets before JSON generation (generator policy, not a device cap). Where a
# budget exceeds the matching FIELD_CAPS entry the pad clips the value on
# display — deliberate (a too-long value must not cost the whole page) and
# surfaced in the configurator as a "the device changes this" callout. The
# contract↔firmware caps themselves are asserted against micropad_core.h in
# tests/integration/test_mqtt_contract.py.
MAX_REFLECTED_STATE_BYTES = 128
MAX_REFLECTED_UNIT_BYTES = 8

# --- automation identity ----------------------------------------------------
AUTOMATION_ID = "micropad_controller"
AUTOMATION_ALIAS = "MicroPad Controller"
