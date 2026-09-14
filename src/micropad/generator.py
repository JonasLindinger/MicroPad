# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Deterministic MQTT page, catalog, and keymap payload generation."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import cast

import yaml

from micropad.constants import (
    AI_NOTICE,
    AUTOMATION_ALIAS,
    AUTOMATION_ID,
    KEY_IDS,
    MAX_CATALOG_PAYLOAD_BYTES,
    MAX_MQTT_PAYLOAD_BYTES,
    MAX_PAGE_PAYLOAD_BYTES,
    MAX_REFLECTED_STATE_BYTES,
    MAX_REFLECTED_UNIT_BYTES,
)
from micropad.keymaps import effective_keymap
from micropad.models import AppConfig, EntitySummary, PageItem
from micropad.page_graph import index_pages

_REFLECTED_ITEM_TYPES = frozenset({"sensor", "number", "light", "switch", "media_player"})
_ENTITY_ID_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")
_SP_TASK_PREFIX = "script.sp_start_"
_SP_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_SERVICE_BY_ACTION_DOMAIN = {
    ("toggle", "light"): "light.toggle",
    ("on", "light"): "light.turn_on",
    ("off", "light"): "light.turn_off",
    ("toggle", "switch"): "switch.toggle",
    ("on", "switch"): "switch.turn_on",
    ("off", "switch"): "switch.turn_off",
    ("press", "script"): "script.turn_on",
    ("press", "button"): "button.press",
    ("press", "scene"): "scene.turn_on",
    ("volume_up", "media_player"): "media_player.volume_up",
    ("volume_down", "media_player"): "media_player.volume_down",
    ("media_next", "media_player"): "media_player.media_next_track",
    ("media_prev", "media_player"): "media_player.media_previous_track",
    ("edit", "number"): "number.set_value",
    ("confirm", "number"): "number.set_value",
}


class GenerationError(ValueError):
    """Raised when configuration cannot produce a valid MQTT payload."""


def _super_productivity_task_id(entity: str) -> str | None:
    if not entity.startswith(_SP_TASK_PREFIX):
        return None
    task_id = entity[len(_SP_TASK_PREFIX):]
    return task_id if _SP_TASK_ID_PATTERN.fullmatch(task_id) else None


_CONTRACT_PATH: Path = Path(__file__).resolve().parents[2] / "contracts" / "mqtt-contract.json"


@lru_cache(maxsize=1)
def load_contract() -> dict[str, object]:
    """Load the canonical versioned MQTT contract as ``dict[str, object]``.

    The contract file is the single source of truth for topics, actions, key
    IDs, item types, and payload ceilings shared by firmware, backend, and
    frontend; every component is bound to it in the integration tasks.
    """
    data: dict[str, object] = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    if data["version"] != 1:
        raise RuntimeError(f"unsupported MQTT contract version: {data['version']}")
    return data


def _contract_topic(key: str) -> str:
    """Resolve one canonical topic string from the contract, e.g. 'event'."""
    topics = cast(dict[str, dict[str, object]], load_contract()["topics"])
    entry = topics[key]
    return str(entry["name"])


def _contract_retain(key: str) -> bool:
    """Resolve the retained flag for one topic key from the contract."""
    topics = cast(dict[str, dict[str, object]], load_contract()["topics"])
    entry = topics[key]
    return bool(entry["retain"])


def _validate_ha_template_inputs(config: AppConfig) -> None:
    """Reject user-controlled Jinja delimiters before embedding MQTT templates."""
    unsafe_tokens = ("{{", "{%", "{#")
    values: list[str] = []
    for page in config.pages:
        values.extend((page.page_id, page.title, page.parent))
        for item in page.items:
            if (
                item.entity
                and _ENTITY_ID_PATTERN.fullmatch(item.entity) is None
                and _super_productivity_task_id(item.entity) is None
            ):
                raise GenerationError("unsafe Home Assistant template syntax in configured value")
            values.extend(
                value for value in item.model_dump(mode="json").values() if isinstance(value, str)
            )
        for binding in effective_keymap(config, page.page_id).values():
            values.extend((binding.action, binding.entity, binding.target_page))
    if any(token in value for value in values for token in unsafe_tokens):
        raise GenerationError("unsafe Home Assistant template syntax in configured value")


def _compact(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _truncate_utf8(value: str, max_bytes: int) -> str:
    """Truncate a dynamic string to a UTF-8 byte budget without splitting a codepoint."""
    raw = value.encode("utf-8")
    if len(raw) <= max_bytes:
        return value
    # Cutting the byte span then decoding with errors='ignore' drops only the
    # partial trailing codepoint, so the result is valid UTF-8 (and valid JSON).
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def _state_index(config: AppConfig, states: list[EntitySummary] | None) -> dict[str, EntitySummary]:
    source = config.entity_cache if states is None else states
    return {entity.entity_id: entity for entity in source}


def _item_payload(item: PageItem, states: dict[str, EntitySummary]) -> dict[str, object]:
    payload = item.model_dump(mode="json")
    reflected = states.get(item.entity)
    if (
        reflected is None
        or item.type not in _REFLECTED_ITEM_TYPES
        or reflected.domain != item.type
        or reflected.state in {"", "unknown", "unavailable"}
    ):
        return payload
    if item.type == "number":
        try:
            value = float(reflected.state)
        except ValueError:
            return payload
        if not math.isfinite(value):
            return payload
        payload["value"] = value
        minimum = reflected.minimum if reflected.minimum is not None else item.min
        maximum = reflected.maximum if reflected.maximum is not None else item.max
        step = reflected.step if reflected.step is not None else item.step
        if (
            math.isfinite(minimum)
            and math.isfinite(maximum)
            and math.isfinite(step)
            and minimum <= maximum
            and step > 0
        ):
            payload["min"] = minimum
            payload["max"] = maximum
            payload["step"] = step
    payload["state"] = _truncate_utf8(reflected.state, MAX_REFLECTED_STATE_BYTES)
    if item.type == "sensor" and reflected.unit:
        payload["unit"] = _truncate_utf8(reflected.unit, MAX_REFLECTED_UNIT_BYTES)
    return payload


def generate_page_payload(
    config: AppConfig,
    page_id: str,
    states: list[EntitySummary] | None = None,
) -> dict[str, object]:
    """Generate one detached page payload, reflecting available HA state."""
    page = index_pages(config.pages).get(page_id)
    if page is None:
        raise GenerationError(f"unknown page_id: {page_id}")
    state_by_id = _state_index(config, states)
    payload: dict[str, object] = {
        "page_id": page.page_id,
        "title": page.title,
        "parent": page.parent,
        "items": [_item_payload(item, state_by_id) for item in page.items],
    }
    size = len(_compact(payload).encode("utf-8"))
    if size > MAX_PAGE_PAYLOAD_BYTES:
        raise GenerationError(f"page {page.page_id} exceeds {MAX_PAGE_PAYLOAD_BYTES} bytes: {size}")
    return payload


def generate_catalog_payload(config: AppConfig, states: list[EntitySummary] | None = None) -> str:
    """Generate the full ordered boot-time page catalog as compact JSON."""
    pages = [generate_page_payload(config, page.page_id, states) for page in config.pages]
    payload = _compact({"pages": pages})
    size = len(payload.encode("utf-8"))
    if size > MAX_CATALOG_PAYLOAD_BYTES:
        raise GenerationError(f"catalog exceeds {MAX_CATALOG_PAYLOAD_BYTES} bytes: {size}")
    return payload


def validate_generated_bounds(config: AppConfig) -> None:
    """Generate every publishable payload and reject any that exceeds its limit."""
    for page in config.pages:
        generate_page_payload(config, page.page_id)
        generate_keymap_payload(config, page.page_id)
    generate_catalog_payload(config)


def generate_keymap_payload(config: AppConfig, page_id: str) -> str:
    """Generate a complete effective keymap in canonical physical-key order."""
    if page_id not in index_pages(config.pages):
        raise GenerationError(f"unknown page_id: {page_id}")
    bindings = effective_keymap(config, page_id)
    payload = _compact({key_id: bindings[key_id].model_dump(mode="json") for key_id in KEY_IDS})
    size = len(payload.encode("utf-8"))
    if size > MAX_MQTT_PAYLOAD_BYTES:
        raise GenerationError(f"keymap {page_id} exceeds {MAX_MQTT_PAYLOAD_BYTES} bytes: {size}")
    return payload


def _event_condition(
    action: str,
    entity: str = "",
    page_id: str = "",
    target_page: str = "",
    no_target: bool = False,
) -> list[dict[str, str]]:
    """Build an HA automation condition on the uc event payload (P1.1).

    ``no_target`` restricts the match to events WITHOUT a target page, so an
    origin keymap publication never also matches the target-page branch.
    """
    checks = [f"event.action == {json.dumps(action)}"]
    if entity:
        checks.append(f"event.entity == {json.dumps(entity)}")
    if page_id:
        checks.append(f"event.page_id == {json.dumps(page_id)}")
    if target_page:
        checks.append(f"event.target_page == {json.dumps(target_page)}")
    if no_target:
        checks.append("event.target_page | default('') == ''")
    return [{"condition": "template", "value_template": "{{ " + " and ".join(checks) + " }}"}]


def _service_step(service: str, entity: str) -> dict[str, object]:
    step: dict[str, object] = {"action": service, "target": {"entity_id": entity}}
    if service == "number.set_value":
        step["data"] = {"value": "{{ event.value }}"}
    return step


def _runtime_item_template(item: PageItem) -> str:
    payload = item.model_dump(mode="json")
    runtime_values: dict[str, str] = {}
    if item.entity and _super_productivity_task_id(item.entity) is None:
        runtime_values["state"] = f"{{{{ states({item.entity!r}) | to_json }}}}"
    if item.type == "number":
        runtime_values["value"] = f"{{{{ states({item.entity!r}) | float(default={item.value}) }}}}"
    if item.type == "media_player":
        runtime_values["value"] = (
            f"{{{{ (state_attr({item.entity!r}, 'volume_level') | float(default=0) * 100) "
            "| round(0) }}"
        )
    fields = [
        _compact(key) + ":" + runtime_values.get(key, _compact(value))
        for key, value in payload.items()
    ]
    return "{" + ",".join(fields) + "}"


def generate_page_template(config: AppConfig, page_id: str) -> str:
    """Generate compact page JSON with safe concrete runtime-state templates."""
    _validate_ha_template_inputs(config)
    page = index_pages(config.pages).get(page_id)
    if page is None:
        raise GenerationError(f"unknown page_id: {page_id}")
    fields = [
        _compact("page_id") + ":" + _compact(page.page_id),
        _compact("title") + ":" + _compact(page.title),
        _compact("parent") + ":" + _compact(page.parent),
        _compact("items")
        + ":["
        + ",".join(_runtime_item_template(item) for item in page.items)
        + "]",
    ]
    return "{" + ",".join(fields) + "}"


def generate_catalog_template(config: AppConfig) -> str:
    """Generate the ordered full-page catalog with runtime state templates."""
    page_templates = [generate_page_template(config, page.page_id) for page in config.pages]
    return '{"pages":[' + ",".join(page_templates) + "]}"


def _publish(topic_key: str, payload: str) -> dict[str, object]:
    return {
        "action": "mqtt.publish",
        "data": {
            "topic": _contract_topic(topic_key),
            "payload": payload,
            "retain": _contract_retain(topic_key),
            "qos": 0,
        },
    }


def _page_publications(config: AppConfig, page_id: str) -> list[dict[str, object]]:
    return [
        _publish("current_page", generate_page_template(config, page_id)),
        _publish("keymap", generate_keymap_payload(config, page_id)),
    ]


def _all_publications(config: AppConfig) -> list[dict[str, object]]:
    return [
        _publish("pages", generate_catalog_template(config)),
        *_page_publications(config, "home"),
    ]


def generate_automation(config: AppConfig) -> dict[str, object]:
    """Generate one concrete allow-listed Home Assistant MQTT automation."""
    _validate_ha_template_inputs(config)
    validate_generated_bounds(config)
    choices: list[dict[str, object]] = []
    for page in config.pages:
        for item in page.items:
            task_id = _super_productivity_task_id(item.entity)
            if task_id is not None:
                choices.append(
                    {
                        "conditions": _event_condition("press", item.entity, page.page_id),
                        "sequence": [
                            {
                                "action": "super_productivity.start_task",
                                "data": {"task_id": task_id},
                            },
                            *_page_publications(config, page.page_id),
                        ],
                    }
                )
                continue
            domain = item.entity.partition(".")[0]
            for (action, allowed_domain), service in _SERVICE_BY_ACTION_DOMAIN.items():
                if domain != allowed_domain:
                    continue
                conditions: list[dict[str, str]] = _event_condition(
                    action, item.entity, page.page_id
                )
                if service == "number.set_value":
                    conditions.append(
                        {
                            "condition": "template",
                            "value_template": (
                                "{{ event.value is number and "
                                f"event.value >= {item.min} and "
                                f"event.value <= {item.max} and "
                                f"((((event.value - {item.min}) / {item.step}) | round(9)) == "
                                f"(((event.value - {item.min}) / {item.step}) | round(0)))"
                                " }}"
                            ),
                        }
                    )
                choices.append(
                    {
                        "conditions": conditions,
                        "sequence": [
                            _service_step(service, item.entity),
                            *_page_publications(config, page.page_id),
                        ],
                    }
                )
    for page in config.pages:
        choices.append(
            {
                "conditions": _event_condition("navigate", target_page=page.page_id),
                "sequence": _page_publications(config, page.page_id),
            }
        )
        choices.append(
            {
                # P1.1: a keymap binding with a target page really requests that
                # page's keymap; an origin-only event still publishes the origin
                # page's map (no_target keeps the two branches exclusive).
                "conditions": _event_condition(
                    "keymap", target_page=page.page_id
                ),
                "sequence": [
                    _publish(
                        "keymap",
                        generate_keymap_payload(config, page.page_id),
                    )
                ],
            }
        )
        choices.append(
            {
                "conditions": _event_condition(
                    "keymap", page_id=page.page_id, no_target=True
                ),
                "sequence": [
                    _publish(
                        "keymap",
                        generate_keymap_payload(config, page.page_id),
                    )
                ],
            }
        )
    choices.extend(
        [
            {
                "conditions": _event_condition("home"),
                "sequence": _page_publications(config, "home"),
            },
            {
                "conditions": _event_condition("get_all_pages"),
                "sequence": _all_publications(config),
            },
        ]
    )
    triggers: list[dict[str, object]] = [
        {"trigger": "mqtt", "topic": _contract_topic("event"), "id": "micropad_event"},
        {"trigger": "homeassistant", "event": "start", "id": "ha_start"},
    ]
    top_level_choices: list[dict[str, object]] = [
        {
            "conditions": [{"condition": "trigger", "id": "ha_start"}],
            "sequence": _all_publications(config),
        }
    ]
    if len(config.pages) == 1:
        entity_ids = sorted({item.entity for item in config.pages[0].items if item.entity})
        for index, entity_id in enumerate(entity_ids):
            trigger_id = f"entity_state_{index}"
            triggers.append({"trigger": "state", "entity_id": entity_id, "id": trigger_id})
            top_level_choices.append(
                {
                    "conditions": [{"condition": "trigger", "id": trigger_id}],
                    "sequence": _page_publications(config, "home"),
                }
            )
    top_level_choices.append(
        {
            "conditions": [{"condition": "trigger", "id": "micropad_event"}],
            "sequence": [
                {"variables": {"event": "{{ trigger.payload_json }}"}},
                {"choose": choices, "default": []},
            ],
        }
    )
    return {
        "id": AUTOMATION_ID,
        "alias": AUTOMATION_ALIAS,
        "mode": "queued",
        "max": 20,
        "triggers": triggers,
        "conditions": [],
        "actions": [
            {
                "choose": top_level_choices,
                "default": [],
            }
        ],
    }


@dataclass(frozen=True)
class GeneratedBundle:
    """Complete deterministic artifacts needed for a later deployment."""

    automation: dict[str, object]
    yaml_text: str
    catalog_payload: str
    home_payload: str
    home_keymap_payload: str


def canonical_automation(automation: dict[str, object]) -> dict[str, object]:
    """Return only deployable automation fields in canonical order."""
    keys = ("id", "alias", "mode", "max", "triggers", "conditions", "actions")
    return {key: automation[key] for key in keys}


def automation_yaml(automation: dict[str, object]) -> str:
    """Serialize an automation deterministically with the required notice."""
    return f"# {AI_NOTICE}\n" + yaml.safe_dump(automation, sort_keys=False, allow_unicode=True)


def generate_bundle(
    config: AppConfig, states: list[EntitySummary] | None = None
) -> GeneratedBundle:
    """Generate all validated HA automation and initial MQTT artifacts."""
    automation = generate_automation(config)
    return GeneratedBundle(
        automation=automation,
        yaml_text=automation_yaml(automation),
        catalog_payload=generate_catalog_payload(config, states),
        home_payload=_compact(generate_page_payload(config, "home", states)),
        home_keymap_payload=generate_keymap_payload(config, "home"),
    )
