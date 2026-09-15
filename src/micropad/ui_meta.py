# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Grouped UI metadata and editable page templates for the web configurator."""

from __future__ import annotations

from micropad.constants import ACTION_META, ITEM_TYPE_META


def action_metadata() -> list[dict[str, str]]:
    """Return the complete ordered action table used by the key editor.

    Generated from ``contracts/mqtt-contract.json`` (``action_meta``): adding an
    action there is all it takes for the firmware header, ``/api/meta``, the key
    editor and the browser to agree.
    """
    return [dict(entry) for entry in ACTION_META]


def item_type_metadata() -> list[dict[str, object]]:
    """Return the item-type descriptors (HA domains, default action, flags).

    Mirrors the firmware's ``ITEM_TYPE_DESCRIPTORS`` table (micropad_core.h) so
    the editor can pick the right default action and requirement per type
    instead of hard-coding the mapping in JavaScript.
    """
    return [dict(entry) for entry in ITEM_TYPE_META]


def _item(name: str, item_type: str, entity: str = "") -> dict[str, object]:
    return {
        "name": name,
        "type": item_type,
        "entity": entity,
        "state": "",
        "value": 0,
        "min": 0,
        "max": 100,
        "step": 1,
        "unit": "",
        "editable": False,
        "target_page": "",
    }


def page_templates() -> list[dict[str, object]]:
    """Return editable starter pages for common MicroPad uses."""
    return [
        {
            "id": "spotify",
            "label": "Spotify",
            "description": "Spotify playback and volume",
            "page": {
                "title": "Spotify",
                "items": [
                    _item("Spotify", "media_player", "media_player.spotify"),
                    _item("Next track", "button", "button.spotify_next"),
                    _item("Previous track", "button", "button.spotify_previous"),
                ],
            },
        },
        {
            "id": "discord",
            "label": "Discord",
            "description": "Discord script shortcuts",
            "page": {
                "title": "Discord",
                "items": [
                    _item("Mute", "script", "script.discord_mute"),
                    _item("Deafen", "script", "script.discord_deafen"),
                ],
            },
        },
        {
            "id": "lights",
            "label": "Lights",
            "description": "Room light controls",
            "page": {
                "title": "Lights",
                "items": [_item("Main light", "light", "light.main_light")],
            },
        },
        {
            "id": "generic-media",
            "label": "Generic media",
            "description": "Media-player controls",
            "page": {
                "title": "Media",
                "items": [_item("Media player", "media_player", "media_player.example")],
            },
        },
    ]
