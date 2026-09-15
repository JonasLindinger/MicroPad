# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Build pages from the Home Assistant entity cache (B5).

"One page per room" is the goal; the honest starting point is one page per domain,
because that is what the Home Assistant REST API the configurator already talks to
can tell us (``/api/states`` carries the domain and the friendly name, not the area
registry). Everything below is therefore derived, never guessed:

* which domains are pageable, and which item type each maps to, comes from the
  contract's ``item_type_meta`` rows (the same rows the firmware's descriptor table
  and the editor's validation use);
* the page and item ceilings come from the contract's ``caps``;
* names, units and states are taken from the entity cache and clipped to the
  firmware's field caps, with a note whenever something was shortened, so the
  operator is never handed a page that silently differs on the device.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from micropad.constants import (
    FIELD_CAPS,
    ITEM_TYPE_META,
    MAX_ITEMS_PER_PAGE,
    MAX_PAGES,
)

#: HA domain -> item type, read from the contract (sensor accepts binary_sensor too).
DOMAIN_TO_ITEM_TYPE: dict[str, str] = {
    str(domain): str(entry["id"])
    for entry in ITEM_TYPE_META
    for domain in entry["ha_domains"]
}
#: Item type -> display label, for page titles.
ITEM_TYPE_LABELS: dict[str, str] = {
    str(entry["id"]): str(entry["label"]) for entry in ITEM_TYPE_META
}
#: Item types grouped on a generated page, in the order the picker should offer them
#: (sensor appears twice in the mapping because it accepts two HA domains).
PAGEABLE_ITEM_TYPES: tuple[str, ...] = tuple(dict.fromkeys(DOMAIN_TO_ITEM_TYPE.values()))


@dataclass
class GeneratedPages:
    """Pages ready to be merged into a configuration, plus what changed."""

    pages: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)  # domain -> entity count

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages": self.pages,
            "notes": self.notes,
            "skipped": self.skipped,
            "limits": {"max_items_per_page": MAX_ITEMS_PER_PAGE, "max_pages": MAX_PAGES},
        }


def group_counts(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Domains present in the cache, with the item type they would map to.

    Returns one row per domain in a stable order so the picker can render buttons
    without the UI knowing the mapping.
    """
    counts: dict[str, int] = {}
    for entity in entities:
        domain = str(entity.get("domain") or str(entity.get("entity_id", "")).partition(".")[0])
        if not domain:
            continue
        counts[domain] = counts.get(domain, 0) + 1
    rows = [
        {
            "domain": domain,
            "count": count,
            "item_type": DOMAIN_TO_ITEM_TYPE.get(domain, ""),
            "pageable": domain in DOMAIN_TO_ITEM_TYPE,
        }
        for domain, count in counts.items()
    ]
    rows.sort(
        key=lambda row: (
            not bool(row["pageable"]),
            -int(str(row["count"])),
            str(row["domain"]),
        )
    )
    return rows


def _clip(value: Any, cap: int, notes: list[str], what: str) -> str:
    text = "" if value is None else str(value)
    if len(text) <= cap:
        return text
    notes.append(f"{what} shortened to {cap} characters (the pad's field cap)")
    return text[:cap]


def _slug(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() else "-" for character in value.lower()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[: FIELD_CAPS["page_id"]]


def _item_for(entity: dict[str, Any], item_type: str, notes: list[str]) -> dict[str, Any]:
    """One page item for an entity, in the shape the configuration model expects."""
    name = _clip(entity.get("friendly_name") or entity.get("entity_id", ""),
                 FIELD_CAPS["name"], notes, f'"{entity.get("entity_id", "")}" name')
    item: dict[str, Any] = {
        "name": name,
        "type": item_type,
        "entity": str(entity.get("entity_id", "")),
        "state": _clip(entity.get("state", ""), FIELD_CAPS["state"], notes,
                       f'"{entity.get("entity_id", "")}" state'),
        "unit": _clip(entity.get("unit", ""), FIELD_CAPS["unit"], notes,
                      f'"{entity.get("entity_id", "")}" unit'),
        "value": 0,
        "min": 0,
        "max": 100,
        "step": 1,
        "editable": item_type == "number",
        "target_page": "",
    }
    if item_type == "number":
        # A number item edits a value, so carry the entity's real range instead of
        # the 0..100 placeholder: the pad's editor steps within these bounds.
        minimum = float(entity.get("minimum") or 0)
        maximum = float(entity.get("maximum") or 100)
        step = float(entity.get("step") or 1)
        if minimum > maximum:  # a swapped range in HA must not produce a broken editor
            minimum, maximum = maximum, minimum
        item["min"], item["max"], item["step"] = minimum, maximum, step
    return item


def build_pages(
    entities: list[dict[str, Any]],
    *,
    domains: list[str] | None = None,
    existing_page_ids: list[str] | None = None,
    existing_page_count: int = 0,
) -> GeneratedPages:
    """Group the entity cache into page drafts, one page per domain (split when full).

    ``domains`` selects which domains to build (the picker's buttons); the default is
    every pageable domain present in the cache. Pages are returned in the order the
    picker lists them, never mutate the input, and stop once the pad's page ceiling
    is reached — each of those decisions is reported in ``notes``.
    """
    result = GeneratedPages()
    selected = {domain.lower() for domain in domains} if domains else None
    taken = {str(page_id) for page_id in (existing_page_ids or [])}
    room_for_pages = max(0, MAX_PAGES - max(0, existing_page_count))

    by_domain: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        domain = str(entity.get("domain") or str(entity.get("entity_id", "")).partition(".")[0])
        item_type = DOMAIN_TO_ITEM_TYPE.get(domain, "")
        if not item_type or (selected is not None and domain not in selected):
            if domain:
                result.skipped[domain] = result.skipped.get(domain, 0) + 1
            continue
        by_domain.setdefault(domain, []).append(entity)

    for domain in sorted(by_domain, key=lambda name: (PAGEABLE_ITEM_TYPES.index(
            DOMAIN_TO_ITEM_TYPE[name]), name)):
        item_type = DOMAIN_TO_ITEM_TYPE[domain]
        label = ITEM_TYPE_LABELS.get(item_type, item_type.title())
        entities_in_domain = sorted(
            by_domain[domain],
            key=lambda entity: str(entity.get("friendly_name") or entity.get("entity_id", "")).lower(),
        )
        chunks = [
            entities_in_domain[index:index + MAX_ITEMS_PER_PAGE]
            for index in range(0, len(entities_in_domain), MAX_ITEMS_PER_PAGE)
        ]
        for index, chunk in enumerate(chunks, start=1):
            if len(result.pages) >= room_for_pages:
                result.notes.append(
                    f"Stopped at the pad's {MAX_PAGES}-page ceiling; "
                    f"{len(chunks) - index + 1} {label.lower()} page(s) were not built."
                )
                break
            base = _slug(domain)
            page_id = base if index == 1 else f"{base}-{index}"
            suffix = 1
            while page_id in taken or not page_id:
                page_id = f"{base}-{index + suffix}"
                suffix += 1
            taken.add(page_id)
            title = label if index == 1 else f"{label} {index}"
            result.pages.append(
                {
                    "page_id": page_id,
                    "title": title[: FIELD_CAPS["title"]],
                    "parent": "home",
                    "items": [_item_for(entity, item_type, result.notes) for entity in chunk],
                    "keymap": {},
                }
            )
        if len(result.pages) >= room_for_pages:
            break

    if result.skipped:
        detail = ", ".join(f"{domain} ({count})" for domain, count in sorted(result.skipped.items()))
        result.notes.append(f"Not pageable with the current item types: {detail}.")
    return result
