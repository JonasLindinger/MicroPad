# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
import json

import pytest
import yaml
from jinja2 import Environment

from micropad.constants import (
    AI_NOTICE,
    AUTOMATION_ID,
    CATALOG_TOPIC,
    CURRENT_PAGE_TOPIC,
    EVENT_TOPIC,
    KEY_IDS,
    KEYMAP_TOPIC,
)
from micropad.generator import (
    GenerationError,
    automation_yaml,
    canonical_automation,
    generate_automation,
    generate_bundle,
    generate_catalog_payload,
    generate_catalog_template,
    generate_keymap_payload,
    generate_page_payload,
    generate_page_template,
    validate_generated_bounds,
)
from micropad.models import AppConfig, EntitySummary, Page, PageItem, default_config


def config_with_item(item: PageItem) -> AppConfig:
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [item]
    return config


def find_mqtt_branch(
    automation: dict[str, object],
    *,
    action: str,
    entity: str = "",
    page_id: str = "",
    target_page: str = "",
) -> dict[str, object]:
    top_level_choices = automation["actions"][0]["choose"]
    mqtt_path = next(
        branch
        for branch in top_level_choices
        if branch["conditions"] == [{"condition": "trigger", "id": "micropad_event"}]
    )
    branches = mqtt_path["sequence"][1]["choose"]
    required = [f"event.action == {json.dumps(action)}"]
    for field, value in (("entity", entity), ("page_id", page_id), ("target_page", target_page)):
        if value:
            required.append(f"event.{field} == {json.dumps(value)}")
    matches = [
        branch
        for branch in branches
        if all(text in json.dumps(branch["conditions"]).replace('\\"', '"') for text in required)
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(
    ("item_type", "entity", "action", "service"),
    [
        ("light", "light.desk", "toggle", "light.toggle"),
        ("light", "light.desk", "on", "light.turn_on"),
        ("light", "light.desk", "off", "light.turn_off"),
        ("switch", "switch.fan", "toggle", "switch.toggle"),
        ("switch", "switch.fan", "on", "switch.turn_on"),
        ("switch", "switch.fan", "off", "switch.turn_off"),
        ("script", "script.movie", "press", "script.turn_on"),
        ("button", "button.reboot", "press", "button.press"),
        ("scene", "scene.relax", "press", "scene.turn_on"),
        ("media_player", "media_player.speaker", "volume_up", "media_player.volume_up"),
        ("media_player", "media_player.speaker", "volume_down", "media_player.volume_down"),
        ("media_player", "media_player.speaker", "media_next", "media_player.media_next_track"),
        (
            "media_player",
            "media_player.speaker",
            "media_prev",
            "media_player.media_previous_track",
        ),
        ("number", "number.level", "edit", "number.set_value"),
        ("number", "number.level", "confirm", "number.set_value"),
    ],
)
def test_automation_maps_only_supported_domain_actions(
    item_type: str, entity: str, action: str, service: str
) -> None:
    config = config_with_item(
        PageItem(name="Target", type=item_type, entity=entity, editable=item_type == "number")
    )

    branch = find_mqtt_branch(
        generate_automation(config), action=action, entity=entity, page_id="home"
    )

    expected: dict[str, object] = {"action": service, "target": {"entity_id": entity}}
    if item_type == "number":
        expected["data"] = {"value": "{{ event.value }}"}
    assert branch["sequence"][0] == expected


def test_super_productivity_virtual_task_dispatches_start_task() -> None:
    entity = "script.sp_start_01ABC-def_XYZ"
    config = config_with_item(PageItem(name="Task", type="script", entity=entity))

    branch = find_mqtt_branch(
        generate_automation(config), action="press", entity=entity, page_id="home"
    )

    assert branch["sequence"][0] == {
        "action": "super_productivity.start_task",
        "data": {"task_id": "01ABC-def_XYZ"},
    }


def test_full_twenty_item_super_productivity_page_fits_protocol() -> None:
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [
        PageItem(
            name=f"Super Productivity task number {index} with a useful title",
            type="script",
            entity=f"script.sp_start_01ABC{index:02d}-def_XYZ",
        )
        for index in range(20)
    ]

    assert len(generate_page_payload(config, "home")["items"]) == 20


def test_mqtt_payload_is_parsed_once_and_only_in_the_mqtt_path() -> None:
    automation = yaml.safe_load(
        automation_yaml(
            generate_automation(
                config_with_item(
                    PageItem(
                        name="Level",
                        type="number",
                        entity="number.level",
                        min=0.1,
                        max=1.0,
                        step=0.1,
                        editable=True,
                    )
                )
            )
        )
    )
    top_level = automation["actions"][0]
    mqtt_paths = [
        branch
        for branch in top_level["choose"]
        if branch["conditions"] == [{"condition": "trigger", "id": "micropad_event"}]
    ]

    assert len(mqtt_paths) == 1
    mqtt_sequence = mqtt_paths[0]["sequence"]
    assert mqtt_sequence[0] == {"variables": {"event": "{{ trigger.payload_json }}"}}
    assert "trigger.payload_json" not in json.dumps(top_level["choose"][:-1])
    assert json.dumps(automation).count("trigger.payload_json") == 1
    assert "event.action" in json.dumps(mqtt_sequence[1])
    assert "event.value" in json.dumps(mqtt_sequence[1])


def test_generated_targets_are_concrete_and_unsupported_pairs_have_no_branch() -> None:
    automation = generate_automation(
        config_with_item(PageItem(name="Desk", type="light", entity="light.desk"))
    )
    encoded = json.dumps(automation)
    mqtt_path = automation["actions"][0]["choose"][-1]
    branches = mqtt_path["sequence"][1]["choose"]

    assert "event.entity }}" not in encoded
    assert "light.desk" in encoded
    assert "call-service" not in encoded
    assert not any("press" in json.dumps(branch["conditions"]) for branch in branches)
    assert not any("script.toggle" in json.dumps(branch) for branch in branches)


@pytest.mark.parametrize(
    "item",
    [
        PageItem(name="{{ states('sensor.secret') }}", type="back"),
        PageItem(name="Desk", type="light", entity="light.desk{{7*7}}"),
        PageItem(name="Desk", type="light", entity='light.desk" or true'),
    ],
)
def test_automation_rejects_configured_template_injection(item: PageItem) -> None:
    with pytest.raises(GenerationError, match="unsafe Home Assistant template syntax"):
        generate_automation(config_with_item(item))


@pytest.mark.parametrize(
    ("runtime_item", "sentinel", "runtime_expression"),
    [
        (
            PageItem(name="Desk", type="light", entity="light.desk"),
            "__home_0_STATE__",
            "states('light.desk') | to_json",
        ),
        (
            PageItem(name="Level", type="number", entity="number.level"),
            "__home_0_VALUE__",
            "states('number.level') | float(default=0)",
        ),
        (
            PageItem(name="Speaker", type="media_player", entity="media_player.speaker"),
            "__home_0_VOLUME__",
            "state_attr('media_player.speaker', 'volume_level')",
        ),
    ],
)
def test_prior_runtime_sentinel_forms_in_configured_strings_remain_literal(
    runtime_item: PageItem, sentinel: str, runtime_expression: str
) -> None:
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [runtime_item, PageItem(name=sentinel, type="back")]

    template = generate_page_template(config, "home")

    assert f'"name":{json.dumps(sentinel)}' in template
    assert template.count(runtime_expression) == 1


def test_runtime_templates_resolve_supported_home_assistant_state() -> None:
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [
        PageItem(name="Desk", type="light", entity="light.desk"),
        PageItem(name="Level", type="number", entity="number.level", value=7),
        PageItem(name="Speaker", type="media_player", entity="media_player.speaker"),
    ]

    page = generate_page_template(config, "home")
    catalog = generate_catalog_template(config)

    assert "states('light.desk') | to_json" in page
    assert "states('number.level') | float(default=7.0)" in page
    assert "state_attr('media_player.speaker', 'volume_level')" in page
    assert catalog == '{"pages":[' + page + "]}"
    assert '"state":""' not in page


def test_runtime_templates_render_to_valid_json() -> None:
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [
        PageItem(name="Desk", type="light", entity="light.desk"),
        PageItem(name="Level", type="number", entity="number.level", value=7),
        PageItem(name="Speaker", type="media_player", entity="media_player.speaker"),
    ]
    environment = Environment(autoescape=False)  # noqa: S701 - mirrors HA templates
    environment.filters["to_json"] = json.dumps
    environment.globals.update(
        states=lambda entity: "7" if entity == "number.level" else "on",
        state_attr=lambda _entity, _attribute: 0.5,
    )

    rendered = environment.from_string(generate_page_template(config, "home")).render()

    payload = json.loads(rendered)
    assert [item["value"] for item in payload["items"][1:]] == [7.0, 50.0]


def test_number_branch_requires_numeric_in_range_step_aligned_value() -> None:
    item = PageItem(
        name="Level",
        type="number",
        entity="number.level",
        min=10,
        max=20,
        step=2,
        editable=True,
    )
    branch = find_mqtt_branch(
        generate_automation(config_with_item(item)),
        action="confirm",
        entity="number.level",
        page_id="home",
    )
    conditions = json.dumps(branch["conditions"])

    assert "is number" in conditions
    assert ">= 10.0" in conditions
    assert "<= 20.0" in conditions
    assert "round" in conditions


@pytest.mark.parametrize(
    ("minimum", "step", "aligned", "misaligned"),
    [
        (0.1, 0.1, 0.3, 0.35),
        (0.3, 0.1, 0.5, 0.55),
    ],
)
def test_decimal_number_step_guard_accepts_aligned_and_rejects_misaligned_values(
    minimum: float, step: float, aligned: float, misaligned: float
) -> None:
    item = PageItem(
        name="Level",
        type="number",
        entity="number.level",
        min=minimum,
        max=1.0,
        step=step,
        editable=True,
    )
    branch = find_mqtt_branch(
        generate_automation(config_with_item(item)),
        action="confirm",
        entity="number.level",
        page_id="home",
    )
    guard = branch["conditions"][-1]["value_template"]
    template = Environment(autoescape=True).from_string(guard)

    assert template.render(event={"value": aligned}) == "True"
    assert template.render(event={"value": misaligned}) == "False"


def test_navigation_home_keymap_and_catalog_publish_retained_complete_payloads() -> None:
    config = default_config().model_copy(deep=True)
    config.pages.append(Page(page_id="room", title="Room", parent="home"))
    automation = generate_automation(config)

    navigate = find_mqtt_branch(automation, action="navigate", target_page="room")
    home = find_mqtt_branch(automation, action="home")
    keymap = find_mqtt_branch(automation, action="keymap", page_id="room")
    catalog = find_mqtt_branch(automation, action="get_all_pages")

    assert [step["data"]["topic"] for step in navigate["sequence"]] == [
        CURRENT_PAGE_TOPIC,
        KEYMAP_TOPIC,
    ]
    assert [step["data"]["topic"] for step in home["sequence"]] == [
        CURRENT_PAGE_TOPIC,
        KEYMAP_TOPIC,
    ]
    assert [step["data"]["topic"] for step in keymap["sequence"]] == [KEYMAP_TOPIC]
    assert [step["data"]["topic"] for step in catalog["sequence"]] == [
        CATALOG_TOPIC,
        CURRENT_PAGE_TOPIC,
        KEYMAP_TOPIC,
    ]
    publications = (
        navigate["sequence"] + home["sequence"] + keymap["sequence"] + catalog["sequence"]
    )
    assert all(step["action"] == "mqtt.publish" for step in publications)
    assert all(step["data"]["retain"] is True for step in publications)
    assert all(step["data"]["qos"] == 0 for step in publications)
    assert json.loads(navigate["sequence"][1]["data"]["payload"]) == json.loads(
        generate_keymap_payload(config, "room")
    )


def test_startup_and_one_page_state_changes_republish_authoritative_home() -> None:
    config = config_with_item(PageItem(name="Desk", type="light", entity="light.desk"))

    automation = generate_automation(config)

    assert automation["triggers"][:2] == [
        {"trigger": "mqtt", "topic": EVENT_TOPIC, "id": "micropad_event"},
        {"trigger": "homeassistant", "event": "start", "id": "ha_start"},
    ]
    assert automation["triggers"][2] == {
        "trigger": "state",
        "entity_id": "light.desk",
        "id": "entity_state_0",
    }
    choices = automation["actions"][0]["choose"]
    assert [step["data"]["topic"] for step in choices[0]["sequence"]] == [
        CATALOG_TOPIC,
        CURRENT_PAGE_TOPIC,
        KEYMAP_TOPIC,
    ]
    assert [step["data"]["topic"] for step in choices[1]["sequence"]] == [
        CURRENT_PAGE_TOPIC,
        KEYMAP_TOPIC,
    ]


def test_multi_page_automation_omits_ambiguous_entity_state_triggers() -> None:
    config = config_with_item(PageItem(name="Desk", type="light", entity="light.desk"))
    config.pages.append(Page(page_id="room", title="Room", parent="home"))

    automation = generate_automation(config)

    assert all(trigger["trigger"] != "state" for trigger in automation["triggers"])


def test_automation_yaml_round_trips_deterministically_without_credentials() -> None:
    config = default_config().model_copy(deep=True)
    private_value = "must-not-appear"
    private_key = "also-must-not-appear"
    config.settings.ha_token = private_value
    config.settings.ssh_key = private_key
    automation = generate_automation(config)

    first = automation_yaml(automation)
    second = automation_yaml(generate_automation(config))

    assert first == second
    assert first.startswith(f"# {AI_NOTICE}\n")
    assert yaml.safe_load(first) == automation
    assert "must-not-appear" not in first
    assert "also-must-not-appear" not in first


def test_canonical_automation_preserves_only_deployable_fields_in_order() -> None:
    automation = generate_automation(default_config())
    automation["provider_metadata"] = "ignored"

    canonical = canonical_automation(automation)

    assert tuple(canonical) == ("id", "alias", "mode", "max", "triggers", "conditions", "actions")
    assert canonical["id"] == AUTOMATION_ID
    assert "provider_metadata" not in canonical


def test_generated_bundle_contains_matching_deterministic_artifacts_and_live_state() -> None:
    config = config_with_item(PageItem(name="Desk", type="light", entity="light.desk"))
    states = [
        EntitySummary(entity_id="light.desk", friendly_name="Desk", domain="light", state="on")
    ]

    first = generate_bundle(config, states)
    second = generate_bundle(config, states)

    assert first == second
    assert yaml.safe_load(first.yaml_text) == first.automation
    assert first.catalog_payload == generate_catalog_payload(config, states)
    assert json.loads(first.home_payload)["items"][0]["state"] == "on"
    assert first.home_keymap_payload == generate_keymap_payload(config, "home")


def test_bundle_rejects_oversize_page_before_returning_partial_artifacts() -> None:
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [PageItem(name="é" * 5000, type="back")]

    with pytest.raises(GenerationError, match="page home exceeds 8192 bytes"):
        generate_bundle(config)


def test_checked_in_example_matches_generated_bundle() -> None:
    from pathlib import Path

    from micropad.models import parse_config

    root = Path(__file__).resolve().parents[1]
    config = parse_config(json.loads((root / "config.example.json").read_text(encoding="utf-8")))

    assert (root / "homeassistant" / "automation.example.yaml").read_text(
        encoding="utf-8"
    ) == generate_bundle(config).yaml_text


def test_payloads_reflect_cached_state_without_mutating_config() -> None:
    config = default_config().model_copy(deep=True)
    config.pages[0] = Page(
        page_id="home",
        title="Home",
        items=[PageItem(name="Desk", type="light", entity="light.desk")],
    )
    config.entity_cache = [
        EntitySummary(
            entity_id="light.desk",
            friendly_name="Desk",
            domain="light",
            state="on",
        )
    ]

    page = generate_page_payload(config, "home")
    catalog = json.loads(generate_catalog_payload(config))
    keymap = json.loads(generate_keymap_payload(config, "home"))

    assert page["items"][0]["state"] == "on"
    assert catalog == {"pages": [page]}
    assert tuple(keymap) == KEY_IDS
    assert config.pages[0].items[0].state == ""


def test_unavailable_number_state_preserves_configured_fallbacks() -> None:
    configured = PageItem(
        name="Level",
        type="number",
        entity="number.level",
        state="stale",
        value=42,
        min=1,
        max=99,
        step=0.5,
        editable=True,
    )
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [configured]
    states = [
        EntitySummary(
            entity_id="number.level",
            friendly_name="Level",
            domain="number",
            state="unavailable",
            minimum=0,
            maximum=100,
            step=1,
        )
    ]

    item = generate_page_payload(config, "home", states)["items"][0]

    assert item == configured.model_dump(mode="json")


@pytest.mark.parametrize("state", ["not-a-number", "nan", "inf"])
def test_invalid_number_state_preserves_configured_value(state: str) -> None:
    configured = PageItem(
        name="Level",
        type="number",
        entity="number.level",
        state="stale",
        value=42,
    )
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [configured]
    states = [
        EntitySummary(
            entity_id="number.level",
            friendly_name="Level",
            domain="number",
            state=state,
        )
    ]

    item = generate_page_payload(config, "home", states)["items"][0]

    assert item == configured.model_dump(mode="json")


def test_reflection_is_limited_to_supported_matching_domains() -> None:
    light = PageItem(
        name="Desk",
        type="light",
        entity="light.desk",
        state="configured-light",
    )
    script = PageItem(
        name="Movie",
        type="script",
        entity="script.movie",
        state="configured-script",
    )
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [light, script]
    states = [
        EntitySummary(
            entity_id="light.desk",
            friendly_name="Desk",
            domain="switch",
            state="on",
        ),
        EntitySummary(
            entity_id="script.movie",
            friendly_name="Movie",
            domain="script",
            state="on",
        ),
    ]

    items = generate_page_payload(config, "home", states)["items"]

    assert items[0]["state"] == "configured-light"
    assert items[1]["state"] == "configured-script"


def test_generation_rejects_page_over_utf8_byte_limit() -> None:
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [PageItem(name="é" * 5000, type="back")]

    with pytest.raises(GenerationError, match="page home exceeds 8192 bytes"):
        validate_generated_bounds(config)


def test_generation_rejects_catalog_over_utf8_byte_limit() -> None:
    config = default_config().model_copy(deep=True)
    config.pages = [Page(page_id="home", title="Home")] + [
        Page(page_id=f"page-{index}", title="x" * 700, parent="home") for index in range(23)
    ]

    with pytest.raises(GenerationError, match="catalog exceeds 16000 bytes"):
        validate_generated_bounds(config)


def test_catalog_generation_rejects_oversize_without_separate_validation() -> None:
    config = default_config().model_copy(deep=True)
    config.pages = [Page(page_id="home", title="Home")] + [
        Page(page_id=f"page-{index}", title="x" * 700, parent="home") for index in range(23)
    ]

    with pytest.raises(GenerationError, match="catalog exceeds 16000 bytes"):
        generate_catalog_payload(config)


def test_generation_rejects_keymap_over_mqtt_byte_limit() -> None:
    config = default_config().model_copy(deep=True)
    config.global_keymap["r0c0"].entity = "light." + ("é" * 8200)

    with pytest.raises(GenerationError, match="keymap home exceeds 16380 bytes"):
        validate_generated_bounds(config)


def test_keymap_generation_rejects_oversize_without_separate_validation() -> None:
    config = default_config().model_copy(deep=True)
    config.global_keymap["r0c0"].entity = "light." + ("é" * 8200)

    with pytest.raises(GenerationError, match="keymap home exceeds 16380 bytes"):
        generate_keymap_payload(config, "home")


def test_invalid_number_attributes_do_not_corrupt_configured_bounds() -> None:
    configured = PageItem(
        name="Level",
        type="number",
        entity="number.level",
        value=5,
        min=0,
        max=10,
        step=0.5,
    )
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [configured]
    states = [
        EntitySummary(
            entity_id="number.level",
            friendly_name="Level",
            domain="number",
            state="7.5",
            minimum=20,
            maximum=10,
            step=0,
        )
    ]

    item = generate_page_payload(config, "home", states)["items"][0]

    assert item["state"] == "7.5"
    assert item["value"] == 7.5
    assert (item["min"], item["max"], item["step"]) == (0, 10, 0.5)


def test_unknown_keymap_page_raises_generation_error() -> None:
    with pytest.raises(GenerationError, match="unknown page_id: missing"):
        generate_keymap_payload(default_config(), "missing")


def test_supported_entities_reflect_state_and_attributes() -> None:
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [
        PageItem(name="Temperature", type="sensor", entity="sensor.temperature", unit="F"),
        PageItem(name="Level", type="number", entity="number.level", min=0, max=10),
        PageItem(name="Desk", type="light", entity="light.desk"),
        PageItem(name="Fan", type="switch", entity="switch.fan"),
        PageItem(name="Speaker", type="media_player", entity="media_player.speaker"),
    ]
    states = [
        EntitySummary(
            entity_id="sensor.temperature",
            friendly_name="Temperature",
            domain="sensor",
            state="21.5",
            unit="°C",
        ),
        EntitySummary(
            entity_id="number.level",
            friendly_name="Level",
            domain="number",
            state="7.5",
            minimum=1,
            maximum=9,
            step=0.25,
        ),
        EntitySummary(entity_id="light.desk", friendly_name="Desk", domain="light", state="on"),
        EntitySummary(entity_id="switch.fan", friendly_name="Fan", domain="switch", state="off"),
        EntitySummary(
            entity_id="media_player.speaker",
            friendly_name="Speaker",
            domain="media_player",
            state="playing",
        ),
    ]

    items = generate_page_payload(config, "home", states)["items"]

    assert (items[0]["state"], items[0]["unit"]) == ("21.5", "°C")
    assert (
        items[1]["state"],
        items[1]["value"],
        items[1]["min"],
        items[1]["max"],
        items[1]["step"],
    ) == ("7.5", 7.5, 1, 9, 0.25)
    assert [item["state"] for item in items[2:]] == ["on", "off", "playing"]


def test_explicit_empty_states_do_not_fall_back_to_entity_cache() -> None:
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [
        PageItem(name="Desk", type="light", entity="light.desk", state="configured")
    ]
    config.entity_cache = [
        EntitySummary(entity_id="light.desk", friendly_name="Desk", domain="light", state="on")
    ]

    cached = generate_page_payload(config, "home")
    explicitly_empty = generate_page_payload(config, "home", [])

    assert cached["items"][0]["state"] == "on"
    assert explicitly_empty["items"][0]["state"] == "configured"


def test_live_state_cannot_push_current_page_over_byte_limit() -> None:
    config = default_config().model_copy(deep=True)
    config.pages[0].items = [PageItem(name="Status", type="sensor", entity="sensor.status")]
    states = [
        EntitySummary(
            entity_id="sensor.status",
            friendly_name="Status",
            domain="sensor",
            state="é" * 5000,
        )
    ]

    with pytest.raises(GenerationError, match="page home exceeds 8192 bytes"):
        generate_page_payload(config, "home", states)
