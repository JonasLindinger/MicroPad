# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Config analysis: payload budgets and "what the device will change".

The configurator shows these findings next to the editor (B2/B3 in the roadmap) so
a config that the pad would clip, truncate or refuse is visible *before* it is
published to the broker. Two sources of truth meet here:

* the byte ceilings and field caps from ``contracts/mqtt-contract.json`` (the same
  numbers the firmware reports on its retained ``micropad/device`` payload), and
* the real generated payload sizes from ``micropad.generator``.

Nothing in this module re-implements firmware behaviour: the field-level rules
(name/title/state/unit clip, identifiers reject) mirror the payload parser's
documented policy, and anything that depends on layout or selection is answered by
the firmware itself through ``tools/micropad_sim.cpp``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from micropad.constants import (
    ACTION_META,
    CONTROLS,
    FIELD_CAPS,
    HOME_PAGE_ID,
    ITEM_TYPE_META,
    KEY_IDS,
    MAX_CATALOG_PAYLOAD_BYTES,
    MAX_ITEMS_PER_PAGE,
    MAX_MQTT_PAYLOAD_BYTES,
    MAX_PAGE_PAYLOAD_BYTES,
    MAX_PAGES,
    MQTT_BUFFER_BYTES,
)

#: Display-only fields: the pad clips them and keeps the payload.
CLIPPING_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "title"),
    ("name", "name"),
    ("state", "state"),
    ("unit", "unit"),
)

#: Identifier fields: the pad rejects the whole payload when one is too long.
STRICT_FIELDS: tuple[tuple[str, str], ...] = (
    ("page_id", "page_id"),
    ("entity", "entity"),
    ("target_page", "page_id"),
)

#: Types a slider row may use (contract flag `adjustable`): the ones whose value
#: an encoder turn can move.
ADJUSTABLE_TYPES: frozenset[str] = frozenset(
    str(entry["id"]) for entry in ITEM_TYPE_META if entry.get("adjustable")
)
#: Actions that need a value *direction*: `adjust` steps the selected slider row
#: on an encoder turn, so on a press (or as a gesture) it has nothing to move.
DIRECTIONAL_ACTIONS: frozenset[str] = frozenset({"adjust"})
#: Actions that address an entity: bound without one they are a defined no-op,
#: which is worth saying out loud before it is published.
ENTITY_ARGUMENT_ACTIONS: frozenset[str] = frozenset(
    str(entry["id"]) for entry in ACTION_META if entry["argument"] == "entity"
)
#: The two turns of the rotary encoder. It has no press (no push button on this
#: part), so a hold or double gesture on it can never fire.
ENCODER_KEYS: frozenset[str] = frozenset({"enc_up", "enc_down"})


@dataclass(frozen=True)
class Finding:
    """One reason the device would change or refuse this configuration."""

    code: str
    severity: str  # "error" blocks a publish, "warning" changes what is shown
    message: str
    where: str = ""
    cap: int = 0
    length: int = 0

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.where:
            payload["where"] = self.where
        if self.cap:
            payload["cap"] = self.cap
            payload["length"] = self.length
        return payload


@dataclass
class Analysis:
    """Budgets plus findings for one configuration."""

    findings: list[Finding] = field(default_factory=list)
    page_bytes: dict[str, int] = field(default_factory=dict)
    catalog_bytes: int = 0
    keymap_bytes: int = 0
    limits: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when nothing would be refused by the device."""
        return not any(finding.severity == "error" for finding in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "findings": [finding.as_dict() for finding in self.findings],
            "page_bytes": dict(sorted(self.page_bytes.items())),
            "catalog_bytes": self.catalog_bytes,
            "keymap_bytes": self.keymap_bytes,
            "limits": self.limits,
            "caps": {
                "field_caps": dict(FIELD_CAPS),
                "max_pages": MAX_PAGES,
                "max_items_per_page": MAX_ITEMS_PER_PAGE,
            },
        }


def _field_findings(
    config: Mapping[str, Any], findings: list[Finding]
) -> None:
    """Compare every editable string against the firmware's field caps."""
    pages = config.get("pages")
    if not isinstance(pages, Sequence):
        return
    for index, page in enumerate(pages):
        if not isinstance(page, Mapping):
            continue
        page_id = str(page.get("page_id", f"#{index}"))
        for key, cap_name in CLIPPING_FIELDS + STRICT_FIELDS:
            raw = page.get(key)
            if not isinstance(raw, str):
                continue
            cap = FIELD_CAPS[cap_name]
            if len(raw) <= cap:
                continue
            strict = (key, cap_name) in STRICT_FIELDS
            findings.append(
                Finding(
                    code="identifier_too_long" if strict else "field_clipped",
                    severity="error" if strict else "warning",
                    message=(
                        f'Page "{page_id}" {key} is {len(raw)} characters; the pad '
                        f"{'rejects' if strict else 'clips'} it at {cap}."
                    ),
                    where=f"pages[{index}].{key}",
                    cap=cap,
                    length=len(raw),
                )
            )
        items = page.get("items")
        if not isinstance(items, Sequence):
            continue
        for item_index, item in enumerate(items):
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name", f"#{item_index}"))
            for key, cap_name in CLIPPING_FIELDS + STRICT_FIELDS:
                if key in {"page_id", "title"}:
                    continue  # page-level only
                raw = item.get(key)
                if not isinstance(raw, str):
                    continue
                cap = FIELD_CAPS[cap_name]
                if len(raw) <= cap:
                    continue
                strict = (key, cap_name) in STRICT_FIELDS
                findings.append(
                    Finding(
                        code="identifier_too_long" if strict else "field_clipped",
                        severity="error" if strict else "warning",
                        message=(
                            f'Item "{name}" on page "{page_id}": {key} is {len(raw)} '
                            f"characters; the pad "
                            f"{'rejects the payload' if strict else f'clips it at {cap}'}."
                        ),
                        where=f"pages[{index}].items[{item_index}].{key}",
                        cap=cap,
                        length=len(raw),
                    )
                )


def _control_findings(config: Mapping[str, Any], findings: list[Finding]) -> None:
    """Check every item's `control` field while it is still a draft.

    These rules exist in ``models.PageItem`` too (a slider row that cannot move is
    refused on save), but a draft never reaches that model: the editor shows the
    analysis panel for the document as it is being typed, so the same rules have
    to answer with a `where` path here.
    """
    pages = config.get("pages")
    if not isinstance(pages, Sequence):
        return
    for index, page in enumerate(pages):
        if not isinstance(page, Mapping):
            continue
        page_id = str(page.get("page_id", f"#{index}"))
        items = page.get("items")
        if not isinstance(items, Sequence):
            continue
        for item_index, item in enumerate(items):
            if not isinstance(item, Mapping):
                continue
            control = item.get("control", "button")
            where = f"pages[{index}].items[{item_index}]"
            name = str(item.get("name", f"#{item_index}"))
            if control not in CONTROLS:
                findings.append(
                    Finding(
                        code="unknown_control",
                        severity="error",
                        message=(
                            f'Item "{name}" on page "{page_id}" has control '
                            f"{control!r}; the pad knows {sorted(CONTROLS)}."
                        ),
                        where=f"{where}.control",
                    )
                )
                continue
            if control != "slider":
                continue
            item_type = str(item.get("type", ""))
            if item_type not in ADJUSTABLE_TYPES:
                findings.append(
                    Finding(
                        code="slider_without_adjustable_value",
                        severity="error",
                        message=(
                            f'Item "{name}" on page "{page_id}" is a slider, but a '
                            f"{item_type} row has no adjustable value "
                            f"(sliders need one of {sorted(ADJUSTABLE_TYPES)})."
                        ),
                        where=f"{where}.control",
                    )
                )
            minimum = item.get("min", 0)
            maximum = item.get("max", 100)
            step = item.get("step", 1)
            if (
                isinstance(minimum, (int, float))
                and isinstance(maximum, (int, float))
                and not isinstance(minimum, bool)
                and not isinstance(maximum, bool)
                and minimum >= maximum
            ):
                findings.append(
                    Finding(
                        code="slider_without_range",
                        severity="error",
                        message=(
                            f'Item "{name}" on page "{page_id}" is a slider with '
                            f"min {minimum} and max {maximum}: the encoder would "
                            "have no range to move in."
                        ),
                        where=f"{where}.min",
                    )
                )
            if isinstance(step, (int, float)) and not isinstance(step, bool) and step <= 0:
                findings.append(
                    Finding(
                        code="slider_without_step",
                        severity="error",
                        message=(
                            f'Item "{name}" on page "{page_id}" is a slider with a '
                            f"step of {step}; one encoder turn must move the value."
                        ),
                        where=f"{where}.step",
                    )
                )


def _keymap_findings(config: Mapping[str, Any], findings: list[Finding]) -> None:
    """Check the keymap's gestures and directional actions.

    Three ways to configure something that can never fire, all of them silent on
    the pad: `adjust` on a key without a direction, a hold/double gesture on the
    encoder (the part has no press), and a gesture action that needs an entity
    without one. Each is a warning - the configuration is still publishable.
    """
    scopes: list[tuple[str, Mapping[str, Any]]] = []
    global_keymap = config.get("global_keymap")
    if isinstance(global_keymap, Mapping):
        scopes.append(("global_keymap", global_keymap))
    pages = config.get("pages")
    if isinstance(pages, Sequence):
        for index, page in enumerate(pages):
            if not isinstance(page, Mapping):
                continue
            keymap = page.get("keymap")
            if isinstance(keymap, Mapping):
                scopes.append((f"pages[{index}].keymap", keymap))

    for scope, keymap in scopes:
        for key_id in KEY_IDS:
            binding = keymap.get(key_id)
            if not isinstance(binding, Mapping):
                continue
            action = binding.get("action", "none")
            if action in DIRECTIONAL_ACTIONS and key_id not in ENCODER_KEYS:
                findings.append(
                    Finding(
                        code="directional_action_on_a_key",
                        severity="warning",
                        message=(
                            f"{scope}.{key_id} binds {action!r}, which turns the "
                            "selected slider row when the encoder turns: a key "
                            "press has no direction, so nothing happens. Bind it "
                            "to enc_up/enc_down."
                        ),
                        where=f"{scope}.{key_id}",
                    )
                )
            for gesture_name in ("hold", "double"):
                gesture = binding.get(gesture_name)
                if not isinstance(gesture, Mapping):
                    continue
                gesture_action = gesture.get("action", "none")
                where = f"{scope}.{key_id}.{gesture_name}"
                if gesture_action == "none":
                    if gesture.get("entity") or gesture.get("target_page"):
                        findings.append(
                            Finding(
                                code="gesture_without_action",
                                severity="warning",
                                message=(
                                    f"{where} carries an entity or target page but "
                                    "no action, so the gesture never fires."
                                ),
                                where=where,
                            )
                        )
                    continue
                if key_id in ENCODER_KEYS:
                    findings.append(
                        Finding(
                            code="gesture_on_the_encoder",
                            severity="warning",
                            message=(
                                f"{where} binds {gesture_action!r}, but the encoder "
                                "has no press (only two turns), so a "
                                f"{gesture_name} gesture can never fire there."
                            ),
                            where=where,
                        )
                    )
                if gesture_action in DIRECTIONAL_ACTIONS:
                    findings.append(
                        Finding(
                            code="directional_action_on_a_gesture",
                            severity="warning",
                            message=(
                                f"{where} binds {gesture_action!r}, which needs an "
                                "encoder direction; a gesture carries none."
                            ),
                            where=where,
                        )
                    )
                if (
                    gesture_action in ENTITY_ARGUMENT_ACTIONS
                    and not gesture.get("entity")
                ):
                    findings.append(
                        Finding(
                            code="gesture_without_entity",
                            severity="warning",
                            message=(
                                f"{where} binds {gesture_action!r} without an "
                                "entity: the pad publishes the event, but the "
                                "automation has nothing to address."
                            ),
                            where=where,
                        )
                    )


def analyze(config: Mapping[str, Any]) -> Analysis:
    """Analyse a (possibly still invalid) configuration document.

    Field-level findings need no valid model — they are exactly the ones a draft
    produces while it is being edited. Byte budgets come from the real generator,
    so they are only reported once the config validates; a validation failure is
    returned as an ``config_invalid`` error finding instead of a guess.
    """
    from micropad.generator import (
        GenerationError,
        generate_bundle,
        page_payload_bytes,
    )
    from micropad.models import parse_config

    limits = {
        "page_bytes": MAX_PAGE_PAYLOAD_BYTES,
        "catalog_bytes": MAX_CATALOG_PAYLOAD_BYTES,
        "keymap_bytes": MAX_MQTT_PAYLOAD_BYTES,
        "mqtt_buffer_bytes": MQTT_BUFFER_BYTES,
    }
    analysis = Analysis(limits=limits)
    _field_findings(config, analysis.findings)
    _control_findings(config, analysis.findings)
    _keymap_findings(config, analysis.findings)

    # The document notice is a fixed constant, not user content: the stored model
    # carries it under the `_ai_assisted_notice` alias, while a dump uses the field
    # name. Drop both spellings so a round-tripped config analyses like the stored
    # one instead of failing as "extra inputs are not allowed".
    candidate: dict[str, Any] = {
        key: value
        for key, value in config.items()
        if key not in {"_ai_assisted_notice", "ai_assisted_notice"}
    }
    try:
        parsed = parse_config(candidate)
    except Exception as error:  # pydantic ValidationError and friends
        analysis.findings.append(
            Finding(
                code="config_invalid",
                severity="error",
                message=f"The configuration is not publishable yet: {error}",
            )
        )
        return analysis

    try:
        bundle = generate_bundle(parsed)
    except GenerationError as error:
        analysis.findings.append(
            Finding(
                code="generation_failed",
                severity="error",
                message=f"The generator refused this configuration: {error}",
            )
        )
        return analysis

    analysis.catalog_bytes = len(bundle.catalog_payload.encode("utf-8"))
    analysis.keymap_bytes = len(bundle.home_keymap_payload.encode("utf-8"))
    for page in parsed.pages:
        # Same serializer the deployment publishes with, so the meter shows the
        # real payload size rather than an approximation.
        analysis.page_bytes[page.page_id] = page_payload_bytes(parsed, page.page_id)

    if len(parsed.pages) > MAX_PAGES:
        analysis.findings.append(
            Finding(
                code="too_many_pages",
                severity="error",
                message=(
                    f"{len(parsed.pages)} pages exceed the pad's {MAX_PAGES}; the "
                    "extra pages would never be stored."
                ),
            )
        )
    for page in parsed.pages:
        size = analysis.page_bytes.get(page.page_id)
        if size is not None and size > MAX_PAGE_PAYLOAD_BYTES:
            analysis.findings.append(
                Finding(
                    code="page_payload_too_large",
                    severity="error",
                    message=(
                        f'Page "{page.page_id}" serializes to {size} bytes, over the '
                        f"{MAX_PAGE_PAYLOAD_BYTES}-byte ceiling; the pad would ignore "
                        "the retained payload."
                    ),
                    where=f"pages[{page.page_id}]",
                )
            )
    if analysis.catalog_bytes >= limits["mqtt_buffer_bytes"]:
        analysis.findings.append(
            Finding(
                code="catalog_over_buffer",
                severity="error",
                message=(
                    f"The catalog serializes to {analysis.catalog_bytes} bytes, which "
                    f"does not fit the pad's {limits['mqtt_buffer_bytes']}-byte MQTT "
                    "buffer; PubSubClient would drop it without an error."
                ),
            )
        )
    # Reachability: the pad builds every menu from the *items* of a page, so a page no
    # other page links to is stored, published, and still unreachable on the device.
    # Reported symptom: "the website says there is a Spotify page, the board does not
    # show it" - the page existed, its parent had no menu entry pointing at it.
    linked_pages = {
        item.target_page
        for page in parsed.pages
        for item in page.items
        if item.target_page
    }
    for page in parsed.pages:
        if page.page_id == HOME_PAGE_ID or page.page_id in linked_pages:
            continue
        analysis.findings.append(
            Finding(
                code="page_unreachable",
                severity="warning",
                message=(
                    f'Page "{page.title}" ({page.page_id}) is not linked from any page: '
                    "the pad only reaches pages through a menu item, so this page can "
                    "never be opened on the device. Add a category item pointing at it."
                ),
                where=f"pages[{page.page_id}]",
            )
        )
    return analysis
