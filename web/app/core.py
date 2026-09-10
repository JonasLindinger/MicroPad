"""MicroPad Config Generator — core logic (frontend/tkinter-free).

Pure functions for building MicroPad page payloads and Home Assistant
automations. Extracted from the desktop GUI so the web app can reuse them.
"""

import json
import re

# ---------------------------------------------------------------------------
# Item types & domain mapping
# ---------------------------------------------------------------------------
ITEM_TYPES = ["category", "light", "switch", "script", "button", "sensor",
              "media_player", "number", "settings", "back"]

DOMAIN_MAP = {
    "light": "light",
    "switch": "switch",
    "script": "script",
    "button": "button",
    "sensor": "sensor",
    "media_player": "media_player",
    "number": "number",
}

# Firmware constraints (must match MicroPad_HA_Controller.ino):
MAX_ITEMS_PER_PAGE = 20
MAX_PAYLOAD_BYTES = 1800
ENTITY_REQUIRED_TYPES = {"light", "switch", "script", "button", "sensor",
                         "media_player", "number"}


# ---------------------------------------------------------------------------
# Entity helpers
# ---------------------------------------------------------------------------
def entity_template(entity_id):
    return f"{{{{ states('{entity_id}') }}}}"


def suggest_type(entity_id: str):
    domain = entity_id.split(".")[0] if "." in entity_id else ""
    for key, dom in DOMAIN_MAP.items():
        if domain == dom:
            return key
    return "sensor"


def suggest_name(entity_id: str):
    parts = entity_id.split(".")
    if len(parts) < 2:
        return entity_id
    return parts[1].replace("_", " ").title()


# ---------------------------------------------------------------------------
# Page payload builders
# ---------------------------------------------------------------------------
def build_page_dict(page):
    items_out = []
    for it in page["items"]:
        entry = {"name": it["name"], "type": it["type"]}
        if it.get("entity"):
            entry["entity"] = it["entity"]
            if it["type"] in ("light", "switch"):
                entry["state"] = entity_template(it["entity"])
            elif it["type"] == "sensor":
                entry["state"] = entity_template(it["entity"]) + (it.get("unit", "") or "")
            elif it["type"] == "script":
                entry["state"] = entity_template(it["entity"])
            elif it["type"] == "media_player":
                if it.get("editable"):
                    entry["value"] = f"{{{{ state_attr('{it['entity']}','volume_level') | float(default=0) * 100 | int }}}}"
                    entry["min"] = it.get("min", 0)
                    entry["max"] = it.get("max", 100)
                    entry["step"] = it.get("step", 5)
                    entry["editable"] = True
                else:
                    entry["state"] = entity_template(it["entity"])
        if it.get("target_page"):
            entry["target_page"] = it["target_page"]
        if it.get("min") is not None and it["type"] == "number":
            entry["min"] = it["min"]
            entry["max"] = it["max"]
            entry["step"] = it["step"]
            entry["editable"] = True
            entry["value"] = entity_template(it["entity"])
        items_out.append(entry)

    payload = {
        "page_id": page["id"],
        "title": page["title"],
        "items": items_out,
    }
    if page.get("parent"):
        payload["parent"] = page["parent"]
    return payload


def generate_page_payload(page):
    return json.dumps(build_page_dict(page), ensure_ascii=False, indent=2)


def generate_all_pages_payload(pages):
    """Wrap every page's payload into a single JSON array for the
    'get_all_pages' full-catalog fetch (topic micropad/pages/all)."""
    parts = [generate_page_payload(p) for p in pages]
    return "{\n  \"pages\": [\n" + ",\n".join(parts) + "\n  ]\n}"


# ---------------------------------------------------------------------------
# Automation builder (YAML schema)
# ---------------------------------------------------------------------------
def build_automation_dict(pages):
    """Build the MicroPad automation as a Python dict matching the HA schema
    (the structure both the YAML editor and the Config API expect)."""
    variables = {
        "action": "{{ trigger.payload_json.action }}",
        "entity": "{{ trigger.payload_json.entity | default('') }}",
        "value": "{{ trigger.payload_json.value | default('') }}",
        "target_page": "{{ trigger.payload_json.target_page | default('') }}",
        "page_id": "{{ trigger.payload_json.page_id | default('') }}",
    }

    branches = []

    # HOME / NAVIGATE branches
    for page in pages:
        action = "home" if page["id"] == "home" else "navigate"
        conditions = [f"{{{{ action == '{action}' }}}}"]
        if action == "navigate":
            conditions.append(f"{{{{ target_page == '{page['id']}' }}}}")
        branches.append({
            "conditions": conditions,
            "sequence": [{
                "service": "mqtt.publish",
                "data": {
                    "topic": "micropad/page/current",
                    "payload": generate_page_payload(page),
                }
            }]
        })

    # TOGGLE
    toggle_pages = [p for p in pages if any(
        i["type"] in ("light", "switch") and i.get("entity") for i in p["items"])]
    if toggle_pages:
        payload_parts = ["{% if false %}{% endif %}"]
        for page in toggle_pages:
            payload_parts.append(f"{{% if page_id == '{page['id']}' %}}")
            payload_parts.append(generate_page_payload(page))
            payload_parts.append("{% endif %}")
        branches.append({
            "conditions": ["{{ action == 'toggle' }}"],
            "sequence": [
                {"service": "homeassistant.toggle", "target": {"entity_id": "{{ entity }}"}},
                {
                    "service": "mqtt.publish",
                    "data": {
                        "topic": "micropad/page/current",
                        "retain": True,
                        "payload": "\n".join(payload_parts),
                    }
                }
            ]
        })

    # PRESS
    if any(i["type"] in ("script", "button") and i.get("entity")
           for p in pages for i in p["items"]):
        branches.append({
            "conditions": ["{{ action == 'press' }}"],
            "sequence": [{"service": "script.turn_on", "target": {"entity_id": "{{ entity }}"}}]
        })

    # EDIT (media_player volume + input_number/number slider)
    # NOTE: "light" is deliberately excluded: the firmware's activateItem()
    # always fires "toggle" for type light before edit mode, so a light can
    # never enter edit mode on the pad. Use "number"/input_number for dimming.
    edit_pages = [p for p in pages if any(
        (i["type"] == "media_player" and i.get("editable")) or
        (i["type"] == "number") for i in p["items"])]
    if edit_pages:
        inner = []
        if any(i["type"] == "media_player" and i.get("editable")
               for p in pages for i in p["items"]):
            inner.append({
                "conditions": ["{{ entity.startswith('media_player.') }}"],
                "sequence": [{
                    "service": "media_player.volume_set",
                    "target": {"entity_id": "{{ entity }}"},
                    "data": {"volume_level": "{{ (value | float / 100) | round(2) }}"},
                }]
            })
        if any(i["type"] == "number" for p in pages for i in p["items"]):
            # number.* and input_number.* are DIFFERENT domains with DIFFERENT
            # services. number.* -> number.set_value, input_number.* ->
            # input_number.set_value. A combined condition calling only
            # input_number.set_value silently no-ops on number.* entities.
            inner.append({
                "conditions": ["{{ entity.startswith('number.') }}"],
                "sequence": [{
                    "service": "number.set_value",
                    "target": {"entity_id": "{{ entity }}"},
                    "data": {"value": "{{ value }}"},
                }]
            })
            inner.append({
                "conditions": ["{{ entity.startswith('input_number.') }}"],
                "sequence": [{
                    "service": "input_number.set_value",
                    "target": {"entity_id": "{{ entity }}"},
                    "data": {"value": "{{ value }}"},
                }]
            })
        payload_parts = []
        for page in edit_pages:
            payload_parts.append(f"{{% if page_id == '{page['id']}' %}}")
            payload_parts.append(generate_page_payload(page))
            payload_parts.append("{% endif %}")
        branches.append({
            "conditions": ["{{ action == 'edit' }}"],
            "sequence": [
                {"choose": inner},
                {
                    "service": "mqtt.publish",
                    "data": {
                        "topic": "micropad/page/current",
                        "retain": True,
                        "payload": "\n".join(payload_parts),
                    }
                }
            ]
        })

    # GET_ALL_PAGES — pad requests full catalog once per boot so navigation
    # shows cached pages instantly instead of a blank "Loading..." screen.
    if pages:
        branches.append({
            "conditions": ["{{ action == 'get_all_pages' }}"],
            "sequence": [{
                "service": "mqtt.publish",
                "data": {
                    "topic": "micropad/pages/all",
                    "retain": True,
                    "payload": generate_all_pages_payload(pages),
                },
            }],
        })

    return {
        "alias": "MicroPad Controller",
        "description": "Generated by MicroPad Config Generator",
        "trigger": [{"platform": "mqtt", "topic": "micropad/event"}],
        "variables": variables,
        "action": [{"choose": branches}],
        "mode": "queued",
        "max": 10,
    }


def build_ha_automation_config(pages):
    """Config-API variant: identical structure plus the automation id."""
    config = build_automation_dict(pages)
    config["id"] = "micropad_controller"
    return config


def _yaml_str_representer(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def generate_automation_yaml(pages):
    """Render the automation as YAML for automations.yaml / the UI."""
    import yaml
    yaml.add_representer(str, _yaml_str_representer)
    automation = build_automation_dict(pages)
    automation["id"] = "micropad_controller"
    return yaml.dump([automation], sort_keys=False, allow_unicode=True, width=1000)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_pages(pages):
    """Check pages against constraints the ESP32 firmware actually enforces.

    Returns (errors, warnings). If errors is non-empty the generated config
    WILL silently misbehave on the device.
    """
    errors = []
    warnings = []

    if not pages:
        errors.append("No pages defined.")
        return errors, warnings

    ids = [p["id"] for p in pages]
    if "home" not in ids:
        errors.append(
            "No page with id 'home' exists. The pad sends {\"action\":\"home\"} on "
            "boot and when the Home button is pressed; without a 'home' page the "
            "automation has nothing to reply with and the pad hangs on "
            "'Loading...' forever."
        )

    seen = set()
    for pid in ids:
        if pid in seen:
            errors.append(f"Duplicate page id '{pid}'. Page ids must be unique.")
        seen.add(pid)

    for page in pages:
        pid = page["id"]

        if len(page["items"]) > MAX_ITEMS_PER_PAGE:
            errors.append(
                f"Page '{pid}' has {len(page['items'])} items, but the firmware only "
                f"displays the first {MAX_ITEMS_PER_PAGE} - the rest are silently dropped."
            )

        try:
            payload_size = len(generate_page_payload(page).encode("utf-8"))
            if payload_size > MAX_PAYLOAD_BYTES:
                errors.append(
                    f"Page '{pid}' generates a {payload_size}-byte MQTT payload (limit "
                    f"{MAX_PAYLOAD_BYTES}B, firmware buffer 2048B). It may arrive "
                    f"truncated or fail to parse on the pad."
                )
        except Exception:
            pass

        for it in page["items"]:
            itype = it.get("type", "")
            if itype not in ITEM_TYPES:
                errors.append(f"Page '{pid}', item '{it.get('name', '?')}': unknown type '{itype}'.")
            if itype in ENTITY_REQUIRED_TYPES and not it.get("entity", "").strip():
                errors.append(
                    f"Page '{pid}', item '{it.get('name', '?')}': type '{itype}' requires an "
                    f"entity id, but none is set."
                )
            if itype == "category":
                tp = it.get("target_page", "").strip()
                if not tp:
                    errors.append(f"Page '{pid}', item '{it.get('name', '?')}': type 'category' needs a target_page.")
                elif tp not in ids:
                    errors.append(
                        f"Page '{pid}', item '{it.get('name', '?')}': target_page '{tp}' "
                        f"does not match any defined page id. Navigating there will hang "
                        f"on 'Loading...' forever."
                    )

        if page.get("parent") and page["parent"] not in ids:
            warnings.append(f"Page '{pid}': parent '{page['parent']}' does not match any defined page id.")

    return errors, warnings


# ---------------------------------------------------------------------------
# Feed/parse pages back from a live automation
# ---------------------------------------------------------------------------
def _condition_texts(conditions):
    texts = []
    conditions = conditions if isinstance(conditions, list) else [conditions]
    for c in conditions:
        if isinstance(c, str):
            texts.append(c)
        elif isinstance(c, dict):
            if "value_template" in c:
                texts.append(c["value_template"])
            elif "conditions" in c:
                texts.extend(_condition_texts(c["conditions"]))
    return texts


def parse_automation_to_pages(config):
    """Reverse-engineer GUI pages from a MicroPad automation config.

    Only recovers what can be recovered: page id/title/parent/items with
    name/type/entity/target_page/editable/min/max/step. Templated state/value
    strings are dropped since build_page_dict() recreates them.
    """
    actions = config.get("action") or config.get("actions") or []
    choose = None
    for step in actions:
        if isinstance(step, dict) and "choose" in step:
            choose = step["choose"]
            break
    if choose is None:
        return []

    pages = []
    for branch in choose:
        conds = " ".join(_condition_texts(branch.get("conditions", [])))
        if "action == 'home'" not in conds and "action == 'navigate'" not in conds:
            continue
        m = re.search(r"target_page == '([^']+)'", conds)
        seq = branch.get("sequence", [])
        payload_text = None
        for step in seq:
            if not isinstance(step, dict):
                continue
            data = step.get("data", {})
            svc = step.get("service") or step.get("action")
            if svc == "mqtt.publish" and data.get("topic") == "micropad/page/current":
                payload_text = data.get("payload")
                break
        if not payload_text:
            continue
        try:
            page_json = json.loads(payload_text)
        except Exception:
            continue  # templated refresh payload, not a plain page

        items = []
        for it in page_json.get("items", []):
            items.append({
                "name": it.get("name", ""),
                "type": it.get("type", "category"),
                "entity": it.get("entity", ""),
                "target_page": it.get("target_page", ""),
                "unit": "",
                "editable": bool(it.get("editable", False)),
                "min": it.get("min", 0),
                "max": it.get("max", 100),
                "step": it.get("step", 1),
            })
        pages.append({
            "id": page_json.get("page_id", m.group(1) if m else "home"),
            "title": page_json.get("title", ""),
            "parent": page_json.get("parent", ""),
            "items": items,
        })

    return pages