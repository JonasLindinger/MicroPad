# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Realistic wire-shape fixture builders shared by integration tests.

Every builder emits payloads that satisfy the versioned JSON Schemas under
``contracts/``: :func:`page`, :func:`catalog`, :func:`event`, and :func:`keymap`
return the exact wire shapes, while :func:`valid_config` returns a full typed
:class:`AppConfig` the backend generator accepts. Later integration tasks
(2, 3, 5, 6) bind firmware, backend, and frontend to these shapes.
"""

from __future__ import annotations

from micropad.constants import KEY_IDS
from micropad.models import (
    AppConfig,
    Binding,
    BindingTarget,
    Page,
    PageItem,
    Settings,
)

__all__ = ["catalog", "event", "item", "keymap", "page", "valid_config"]


def _binding(action: str = "none", entity: str = "", target_page: str = "") -> dict[str, str]:
    return {"action": action, "entity": entity, "target_page": target_page}


def item(
    name: str = "Stock",
    item_type: str = "sensor",
    entity: str = "sensor.stock",
    state: str = "",
    value: float = 0.0,
    min_value: float = 0.0,
    max_value: float = 100.0,
    step: float = 1.0,
    unit: str = "",
    editable: bool = False,
    target_page: str = "",
    control: str = "button",
) -> dict[str, object]:
    """Build one page-item object in the canonical wire field order."""
    return {
        "name": name,
        "type": item_type,
        "entity": entity,
        "state": state,
        "value": value,
        "min": min_value,
        "max": max_value,
        "step": step,
        "unit": unit,
        "editable": editable,
        "control": control,
        "target_page": target_page,
    }


def page(
    page_id: str = "home",
    title: str = "Home",
    parent: str = "",
    items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build one page object in canonical wire field order."""
    return {
        "page_id": page_id,
        "title": title,
        "parent": parent,
        "items": [] if items is None else items,
    }


def catalog(pages: list[dict[str, object]] | None = None) -> dict[str, object]:
    """Build a catalog envelope wrapping zero or more pages."""
    return {"pages": [] if pages is None else pages}


def event(
    action: str = "toggle",
    entity: str = "",
    value: float | None = None,
    page_id: str = "",
    target_page: str = "",
) -> dict[str, object]:
    """Build an event object; optional fields are emitted only when set."""
    result: dict[str, object] = {"action": action}
    if entity:
        result["entity"] = entity
    if value is not None:
        result["value"] = value
    if page_id:
        result["page_id"] = page_id
    if target_page:
        result["target_page"] = target_page
    return result


def keymap() -> dict[str, dict[str, str]]:
    """Build a complete fourteen-key map in canonical physical order."""
    return {key_id: _binding() for key_id in KEY_IDS}


def valid_config() -> AppConfig:
    """Build a realistic typed configuration with a nested page graph."""
    return AppConfig(
        settings=Settings(),
        pages=[
            Page(
                page_id="home",
                title="Home",
                items=[
                    PageItem(name="Living Lights", type="light", entity="light.living"),
                    PageItem(name="Bedroom", type="category", target_page="bedroom"),
                    PageItem(name="Office", type="category", target_page="office"),
                ],
            ),
            Page(
                page_id="bedroom",
                title="Bedroom",
                parent="home",
                items=[
                    PageItem(
                        name="Bedroom Light",
                        type="light",
                        entity="light.bedroom",
                        # A slider row: the encoder turns its brightness down/up
                        # while the cursor rests on it, and the pad publishes the
                        # new value on the next adjust event.
                        control="slider",
                        min=0,
                        max=100,
                        step=5,
                    ),
                    PageItem(name="Fan", type="switch", entity="switch.fan"),
                ],
            ),
            Page(
                page_id="office",
                title="Office",
                parent="home",
                items=[
                    PageItem(
                        name="AC",
                        type="number",
                        entity="number.ac",
                        value=21.0,
                        min=16.0,
                        max=30.0,
                        step=0.5,
                        unit="°C",
                        editable=True,
                    ),
                    PageItem(
                        name="Temp",
                        type="sensor",
                        entity="sensor.office_temp",
                        unit="°C",
                    ),
                ],
            ),
        ],
        global_keymap=_typed_keymap(),
    )


def _typed_keymap() -> dict[str, Binding]:
    """The typed keymap for the fixtures: mostly unbound, plus the two gestures.

    One key carries a hold and a double binding and the encoder carries the
    adjust action, so the golden files freeze the published shape of a gesture
    binding and of the slider action too - not just the plain tap rows.
    """
    bindings = {key_id: Binding(**binding) for key_id, binding in keymap().items()}
    bindings["r0c1"] = Binding(
        action="toggle",
        entity="light.living",
        hold=BindingTarget(action="off", entity="light.living"),
        double=BindingTarget(action="navigate", target_page="office"),
    )
    bindings["r0c2"] = Binding(action="none", hold=BindingTarget(action="press", entity="script.movie"))
    bindings["enc_up"] = Binding(action="adjust")
    bindings["enc_down"] = Binding(action="adjust")
    return bindings
