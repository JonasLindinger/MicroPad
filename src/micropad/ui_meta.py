# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Grouped UI metadata and editable page templates for the web configurator."""

from __future__ import annotations

_ACTIONS = (
    ("enter", "Enter", "Navigation", "none"),
    ("back", "Back", "Navigation", "none"),
    ("home", "Home", "Navigation", "none"),
    ("navigate", "Navigate", "Navigation", "target_page"),
    ("keymap", "Keymap", "Navigation", "target_page"),
    ("scroll", "Scroll", "Scrolling", "none"),
    ("scroll_up", "Scroll up", "Scrolling", "none"),
    ("scroll_down", "Scroll down", "Scrolling", "none"),
    ("toggle", "Toggle", "Device", "entity"),
    ("on", "Turn on", "Device", "entity"),
    ("off", "Turn off", "Device", "entity"),
    ("press", "Press", "Device", "entity"),
    ("edit", "Edit", "Device", "entity"),
    ("confirm", "Confirm", "Device", "entity"),
    ("volume_up", "Volume up", "Media", "entity"),
    ("volume_down", "Volume down", "Media", "entity"),
    ("media_next", "Next track", "Media", "entity"),
    ("media_prev", "Previous track", "Media", "entity"),
    ("none", "No action", "System", "none"),
    ("settings", "Settings", "System", "none"),
    ("get_all_pages", "Refresh all pages", "System", "none"),
)


def action_metadata() -> list[dict[str, str]]:
    """Return the complete ordered action table used by the key editor."""
    return [
        {"id": action, "label": label, "group": group, "argument": argument}
        for action, label, group, argument in _ACTIONS
    ]


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
