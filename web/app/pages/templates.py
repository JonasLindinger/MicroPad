"""Built-in page templates for MicroPad Config Generator.

Templates are plain Python dicts that match the page schema the rest of the
app uses (`id`, `title`, `items`, optional `parent`/`keymap`). The frontend
exposes them through the "+ New page" popover so a user can spin up a
Spotify or Discord control page in one click.

Schema invariants (enforced by `core.validate_pages`):

* `id` is snake_case. We `slugify` anything we render to be safe.
* `title` is human-readable, shown on the pad display.
* Each item is an object with at least `name` and `type` (see
  `core.ITEM_TYPES`). Entity-typed items also need an `entity` (snake_case
  Home Assistant entity id).
* Items may carry an optional `action` (a key action id from
  `core.KEY_ACTION_TYPES`) and `metadata` (free-form, only used as in-UI
  documentation hints - the validator ignores them).

The entity ids below match Bastian's actual setup (Spotify via bwschti
account, Discord via pcmitaids HASS.Agent buttons). If yours differ,
change them in the popover after the template is inserted - the template
is just a starting point, not a hard lock.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Helpers (tiny, local - the templates themselves stay pure data)
# ---------------------------------------------------------------------------

_SNAKE_CASE = re.compile(r"[^a-z0-9_]+")


def _slug(s: str) -> str:
    """Lowercase + collapse non-[a-z0-9_] to '_', strip edges. Snakecase."""
    return _SNAKE_CASE.sub("_", s.strip().lower()).strip("_") or "page"


# ---------------------------------------------------------------------------
# Spotify template
# ---------------------------------------------------------------------------
# Layout (12 keys, no subpages -- keeps the pad on a single screen):
#
#   r0c0 Play/Pause   r0c1 Next            r0c2 Previous    r0c3 (Volume Edit)
#   r1c0 Vol -        r1c1 Vol +           r1c2 Shuffle     r1c3 Back -> home
#
# Each item lives on the pad AND is reachable from a key binding. The item's
# `type` follows `core.ITEM_TYPES` (media_player + back). The `action` /
# `metadata` fields are extra hints - they're passed through to the page and
# ignored by the validator, but visible in the UI item editor so the user
# can re-bind if needed.

_SPOTIFY_ENTITY = "media_player.spotify_bwschti"


def spotify_default() -> dict:
    """Return a fresh Spotify page dict (deep copy each call)."""
    return {
        "id": "spotify",
        "title": "Spotify",
        "parent": "home",
        "items": [
            {
                "name": "Play / Pause",
                "type": "media_player",
                "entity": _SPOTIFY_ENTITY,
                "action": "toggle",
                "metadata": {"hint": "Toggles playback on this media_player."},
            },
            {
                "name": "Next",
                "type": "media_player",
                "entity": _SPOTIFY_ENTITY,
                "action": "media_next",
            },
            {
                "name": "Previous",
                "type": "media_player",
                "entity": _SPOTIFY_ENTITY,
                "action": "media_prev",
            },
            {
                "name": "Volume Up",
                "type": "media_player",
                "entity": _SPOTIFY_ENTITY,
                "editable": True,
                "min": 0,
                "max": 100,
                "step": 5,
                "action": "volume_up",
            },
            {
                "name": "Volume Down",
                "type": "media_player",
                "entity": _SPOTIFY_ENTITY,
                # Second editable=true is a no-op so the pad still shows a
                # independent slider for the same entity; the user can keep
                # one and drop the other if they prefer.
                "editable": True,
                "min": 0,
                "max": 100,
                "step": 5,
                "action": "volume_down",
            },
            {
                "name": "Shuffle",
                "type": "media_player",
                "entity": _SPOTIFY_ENTITY,
                "action": "toggle",
                "metadata": {"hint": "Toggles shuffle_set on the media_player."},
            },
            {
                "name": "Back",
                "type": "back",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Discord template
# ---------------------------------------------------------------------------
# Discord control is driven by HASS.Agent buttons on Bastian's box:
#   - mute toggle (button.pcmitaids_discord_mute)
#   - start discord (button.pcmitaids_discord_start)
#   - stop discord (button.pcmitaids_discord_stop)
#   - deafen toggle (button.pcmitaids_discord_deafen)  -- user has to add
#     this themselves; included as an item with a hint.
#
# All keys just `press` the entity -> button.press / script.turn_on in the
# generated automation.

def discord_default() -> dict:
    """Return a fresh Discord page dict."""
    return {
        "id": "discord",
        "title": "Discord",
        "parent": "home",
        "items": [
            {
                "name": "Mute / Unmute Mic",
                "type": "button",
                "entity": "button.pcmitaids_discord_mute",
                "action": "press",
            },
            {
                "name": "Start Discord",
                "type": "button",
                "entity": "button.pcmitaids_discord_start",
                "action": "press",
            },
            {
                "name": "Stop Discord",
                "type": "button",
                "entity": "button.pcmitaids_discord_stop",
                "action": "press",
            },
            {
                "name": "Toggle Deafen",
                "type": "button",
                "entity": "button.pcmitaids_discord_deafen",
                "action": "press",
                "metadata": {
                    "hint": "Need HASS.Agent 'deafen' button - add via "
                             "HASS.Agent settings."
                },
            },
            {"name": "Back", "type": "back"},
        ],
    }


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
BUILTIN_TEMPLATES = {
    "empty": {
        "label": "Empty page",
        "description": "Blank page - just an ID and title.",
        "build": lambda: {"id": _slug("new page"), "title": "New page",
                          "parent": "", "items": []},
    },
    "spotify": {
        "label": "Spotify",
        "description": "Play / pause, next, previous, volume, shuffle, back.",
        "build": spotify_default,
    },
    "discord": {
        "label": "Discord",
        "description": "Mute mic, start/stop Discord, toggle deafen, back.",
        "build": discord_default,
    },
}


__all__ = ["spotify_default", "discord_default", "BUILTIN_TEMPLATES", "_slug"]
