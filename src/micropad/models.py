# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Typed configuration models and deterministic normalization."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Annotated, Literal, get_args
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from micropad.constants import (
    ACTIONS,
    AI_NOTICE,
    ITEM_TYPE_META,
    ITEM_TYPES,
    KEY_IDS,
    MAX_ITEMS_PER_PAGE,
)


def normalize_identifier(value: str) -> str:
    """Normalize a display identifier into a stable lowercase slug."""
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


Action = Literal[
    "none", "enter", "back", "home", "settings", "scroll", "scroll_up",
    "scroll_down", "navigate", "keymap", "get_all_pages", "toggle", "on", "off",
    "press", "volume_up", "volume_down", "media_next", "media_prev", "edit", "confirm",
    "adjust",
]
ItemType = Literal[
    "category", "light", "switch", "script", "button", "scene", "sensor",
    "media_player", "number", "settings", "back", "cover", "fan",
    "input_boolean", "lock",
]
#: How a row is driven: a plain button (a press runs its action) or a slider (the
#: encoder additionally adjusts the row's value while the cursor rests on it).
#: The values come from the contract's ``controls``; a type that carries no
#: adjustable value can never be a slider.
Control = Literal["button", "slider"]
ReloadStrategy = Literal["none", "core_restart"]

if frozenset(get_args(Action)) != ACTIONS:
    raise RuntimeError("Action type and ACTIONS constant differ")
if frozenset(get_args(ItemType)) != ITEM_TYPES:
    raise RuntimeError("ItemType and ITEM_TYPES constant differ")

# Item-type shape rules, taken from the contract's item_type_meta: entity-backed
# types carry their HA domain(s), needs_target_page marks the types that are
# useless without a destination page. The firmware mirrors the same rows in
# micropad_core.h (ITEM_TYPE_DESCRIPTORS); a test asserts the two agree, so the
# editor, the backend and the pad cannot hold different opinions about a type.
_ENTITY_DOMAINS: dict[str, frozenset[str]] = {
    str(entry["id"]): frozenset(str(domain) for domain in entry["ha_domains"])
    for entry in ITEM_TYPE_META
    if entry["ha_domains"]
}
_TARGET_PAGE_TYPES: frozenset[str] = frozenset(
    str(entry["id"]) for entry in ITEM_TYPE_META if entry["needs_target_page"]
)
#: Types whose value an encoder turn may change (contract flag ``adjustable``):
#: a light's brightness, a fan's percentage, a cover position, a media player's
#: volume or a bounded number. Anything else would have nothing to adjust, so
#: models.py refuses ``control: slider`` for it instead of publishing a row the
#: pad can only scroll past.
_ADJUSTABLE_TYPES: frozenset[str] = frozenset(
    str(entry["id"]) for entry in ITEM_TYPE_META if entry.get("adjustable")
)


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and validates mutation."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class BindingTarget(StrictModel):
    """Action plus its optional context strings, shared by taps and gestures."""

    action: Action = "none"
    entity: str = ""
    target_page: str = ""

    @field_validator("entity")
    @classmethod
    def validate_entity_value(cls, value: str) -> str:
        # Free-text entities from the keymap editor are validated server-side
        # (P1.10): a binding entity must be a well-formed Home Assistant entity
        # id, or a Super-Productivity virtual task entity.
        if not value:
            return value
        if _ENTITY_ID_PATTERN.fullmatch(value):
            return value
        if value.startswith(_SP_TASK_PREFIX) and _SP_TASK_ID_PATTERN.fullmatch(
            value[len(_SP_TASK_PREFIX):]
        ):
            return value
        raise ValueError("entity must be a valid Home Assistant entity id")

    @field_validator("target_page")
    @classmethod
    def normalize_target(cls, value: str) -> str:
        return normalize_identifier(value)


class Binding(BindingTarget):
    """Action bound to a physical MicroPad input.

    ``hold`` and ``double`` carry the two gestures of the same key. Both default
    to an empty (``none``) binding, which is exactly the single-press behaviour a
    keymap written before gestures existed has: an empty gesture never fires, so
    old configurations and old published keymaps stay valid and the pad keeps
    acting on the tap. A gesture binding is a full target (its own action, entity
    and target page), because "hold turns *this other* light off" is the point of
    the feature; the payload generator omits an unset gesture so a keymap that
    uses none of them stays as small as it was.
    """

    hold: BindingTarget = Field(default_factory=BindingTarget)
    double: BindingTarget = Field(default_factory=BindingTarget)


_ENTITY_ID_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")
_SP_TASK_PREFIX = "script.sp_start_"
_SP_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class Settings(StrictModel):
    """Home Assistant and SSH deployment settings."""

    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""
    verify_tls: bool = True
    request_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 10.0
    ssh_host: str = ""
    ssh_port: Annotated[int, Field(ge=1, le=65535)] = 22
    ssh_user: str = ""
    ssh_key: str = ""
    ssh_known_hosts: str = "~/.ssh/known_hosts"
    remote_path: str = "/config/automations/micropad.yaml"
    # No free-form remote commands are accepted from the API (P0.2). The remote
    # reload action is one of a small fixed set, defined here and executed
    # against a fixed command in the deployer. "none" skips the reload.
    ssh_reload_strategy: ReloadStrategy = "core_restart"

    @field_validator("ha_url")
    @classmethod
    def validate_ha_url(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("ha_url must be an absolute HTTP or HTTPS URL")
        return value.strip().rstrip("/")

    @field_validator("ssh_host", "ssh_user")
    @classmethod
    def validate_ssh_identifier(cls, value: str) -> str:
        if value and (value.startswith("-") or any(character.isspace() for character in value)):
            raise ValueError("SSH host and user must not contain whitespace or start with '-'")
        return value


class PageItem(StrictModel):
    """Entity or navigation item rendered on a page."""

    name: str
    type: ItemType
    entity: str = ""
    state: str = ""
    value: float = 0
    min: float = 0
    max: float = 100
    step: Annotated[float, Field(gt=0)] = 1
    unit: str = ""
    editable: bool = False
    control: Control = "button"
    target_page: str = ""

    @field_validator("target_page")
    @classmethod
    def normalize_target(cls, value: str) -> str:
        return normalize_identifier(value)

    @field_validator("name")
    @classmethod
    def reject_empty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be empty")
        return value

    @field_validator("value", "min", "max", "step")
    @classmethod
    def reject_non_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> PageItem:
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        if self.control == "slider":
            # A slider without a range to move in, or on a type with no
            # adjustable value, is a row the operator would turn with nothing
            # happening. Both are refused here, where the editor can show why.
            if self.type not in _ADJUSTABLE_TYPES:
                raise ValueError(
                    f"{self.type} items cannot be a slider: the type carries no "
                    "adjustable value (light, fan, cover, media_player and number do)"
                )
            if self.min >= self.max:
                raise ValueError("slider items require min < max")
        if self.type in _TARGET_PAGE_TYPES and not self.target_page:
            raise ValueError(f"{self.type} items require target_page")
        if self.type in _ENTITY_DOMAINS and not self.entity:
            raise ValueError(f"{self.type} items require entity")
        if (
            self.entity
            and self.type in _ENTITY_DOMAINS
            and self.entity.partition(".")[0] not in _ENTITY_DOMAINS[self.type]
        ):
            raise ValueError(f"{self.type} items require an entity in the {self.type} domain")
        return self


class Page(StrictModel):
    """A normalized MicroPad page and its local key overrides."""

    page_id: str
    title: str
    parent: str = ""
    items: list[PageItem] = Field(default_factory=list, max_length=MAX_ITEMS_PER_PAGE)
    keymap: dict[str, Binding] = Field(default_factory=dict)

    @field_validator("page_id", "parent")
    @classmethod
    def normalize_page_id(cls, value: str) -> str:
        return normalize_identifier(value)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be empty")
        return value

    @field_validator("keymap")
    @classmethod
    def validate_override_keys(cls, value: dict[str, Binding]) -> dict[str, Binding]:
        unknown = set(value) - set(KEY_IDS)
        if unknown:
            raise ValueError(f"unknown key IDs: {sorted(unknown)}")
        return value


class EntitySummary(StrictModel):
    """Cached Home Assistant entity metadata used by the configurator."""

    entity_id: str
    friendly_name: str
    domain: str
    state: str = "unknown"
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None


class AppConfig(StrictModel):
    """Root typed configuration persisted by the configurator."""

    ai_assisted_notice: str = Field(default=AI_NOTICE, alias="_ai_assisted_notice")
    schema_version: Literal[1] = 1
    settings: Settings = Field(default_factory=Settings)
    pages: list[Page] = Field(max_length=24)
    entity_cache: list[EntitySummary] = Field(default_factory=list)
    global_keymap: dict[str, Binding]

    @field_validator("global_keymap")
    @classmethod
    def validate_global_keys(cls, value: dict[str, Binding]) -> dict[str, Binding]:
        if set(value) != set(KEY_IDS):
            raise ValueError("global_keymap must contain exactly the known key IDs")
        return value

    @model_validator(mode="after")
    def validate_graph(self) -> AppConfig:
        from micropad.page_graph import validate_page_graph

        validate_page_graph(self.pages)
        if tuple(self.global_keymap) != KEY_IDS:
            raise ValueError("global_keymap must contain all fourteen keys in canonical order")
        return self


_DEFAULT_ACTIONS: dict[str, Action] = {
    "r0c0": "home", "r0c3": "enter", "r1c3": "enter", "r2c3": "back",
    # The encoder's default stays plain scrolling: a turn moves the cursor, as it
    # always did. `adjust` (which steps the value of a slider row instead, and
    # scrolls on rows that are not sliders) is available in the key map for an
    # operator who wants the encoder to drive levels - but imposing it as the
    # default would change what the encoder does on every existing page, which is
    # the operator's decision, not the tool's.
    "enc_up": "scroll_up", "enc_down": "scroll_down",
}


def default_keymap() -> dict[str, Binding]:
    """Build a fresh complete key map in physical key order."""
    return {
        key_id: Binding(action=_DEFAULT_ACTIONS.get(key_id, "none"))
        for key_id in KEY_IDS
    }


def default_config() -> AppConfig:
    """Build a fresh safe default application configuration."""
    return AppConfig(
        pages=[Page(page_id="home", title="Home")],
        global_keymap=default_keymap(),
    )


#: Settings keys written by the pre-clean-room configurator (free-form remote shell
#: commands). Kept as a named constant so the drop-on-load rule below and its tests refer
#: to the same list.
LEGACY_SETTINGS_KEYS: tuple[str, ...] = ("ssh_validate_command", "ssh_reload_command")


def parse_config(data: Mapping[str, object]) -> AppConfig:
    """Validate configuration data after filling absent global bindings."""
    normalized = dict(data)
    # Settings keys the pre-clean-room configurator wrote: free-form remote shell
    # commands, removed by the P0.2 hardening (``ssh_reload_strategy`` replaced them).
    # They are dropped on load rather than rejected, because a config file from the
    # legacy generation must not turn the whole tool into a 422 - that strands the
    # operator's data with no way to read or migrate it. Honouring them is what the
    # hardening forbids; ignoring them is not.
    settings_value = normalized.get("settings")
    if isinstance(settings_value, Mapping):
        normalized["settings"] = {
            key: value
            for key, value in settings_value.items()
            if key not in LEGACY_SETTINGS_KEYS
        }
    supplied_value = normalized.get("global_keymap", {})
    if not isinstance(supplied_value, Mapping):
        return AppConfig.model_validate(normalized)
    supplied = dict(supplied_value)
    defaults = default_keymap()
    normalized["global_keymap"] = {
        **{key_id: defaults[key_id].model_dump(mode="json") for key_id in KEY_IDS},
        **supplied,
    }
    return AppConfig.model_validate(normalized)


def public_config(config: AppConfig) -> dict[str, object]:
    """Serialize configuration while redacting private credential settings."""
    result = config.model_dump(mode="json", by_alias=True)
    settings = result["settings"]
    settings["ha_token_configured"] = bool(settings["ha_token"])
    settings["ssh_key_configured"] = bool(settings["ssh_key"])
    settings["ha_token"] = ""
    settings["ssh_key"] = ""
    return result
