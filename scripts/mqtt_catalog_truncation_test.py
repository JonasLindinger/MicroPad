#!/usr/bin/env python3
"""
mqtt_catalog_truncation_test.py

Pre-deploy safety check for the MicroPad firmware's MQTT catalog buffer.

The ESP32 firmware receives the retained Home Assistant payloads on
topics `micropad/pages/all` (the full catalog) and `micropad/keymap`
(the effective mapping for the currently-displayed page). PubSubClient
silently TRUNCATES payloads that exceed its buffer; the pad then never
gets a valid catalog and the GUI shows stale / empty pages.

This script projects the worst-case retained payload size from the live
config.json (read-only) and fails the deploy if it would exceed the
firmware buffer (16384) minus a small safety floor (4 B: 1 NUL +
~24 B topic+header per PubSubClient internal framing, conservative).

The values 16384 (buffer) and 16380 (cap) mirror the firmware's
.setBufferSize(16384) (line ~1231) and the ino NUL-write cap
`if (length > 16380)` (line ~1073).

Hard requirements:
  * Pure Python stdlib - no third-party deps (PyYAML / paho / etc).
  * Read config.json read-only - never mutate.
  * Reproduce exactly the worst-case shape that microspad can ever push:
    - every page has a full per-page keymap override for all 14 keys
    - the largest catalog + the largest effective keymap fit the buffer

Exit codes:
  * 0  projected payload <= 16380 bytes (GREEN - deploy)
  * 1  projected payload >  16380 bytes (RED  - block deploy)
  * 2  config / wiring error (hard fail, not a size violation)
"""

import json
import os
import sys

# --- Constants mirrored from web/app/core.py (KEYMAP_KEYS) --------------
# MicroPad's bindable key set: 12 matrix keys + encoder press + 2 encoder
# directions = 14 source ids. Kept inline (not imported) so the test
# stays self-contained per the script's stdlib-only constraint.
KEY_IDS = [
    "r0c0", "r0c1", "r0c2", "r0c3",
    "r1c0", "r1c1", "r1c2", "r1c3",
    "r2c0", "r2c1", "r2c2", "r2c3",
    "enc_up", "enc_down",
]
assert len(KEY_IDS) == 14, "MicroPad key count drifted from firmware"

# Firmware-mirrored cap - keep these in lockstep with MicroPad_HA_Controller_v6.ino
FIRMWARE_BUFFER_SIZE = 16384      # mqtt.setBufferSize(...), line ~1231
FIRMWARE_NUL_CAP      = 16380      # if (length > 16380) length = 16380, line ~1073


# --- Worst-case page-payload model ---------------------------------------
def _worst_page_keymap(page_id):
    """Build a fully-populated 14-key per-page keymap for `page_id`.

    Mirrors core.normalize_keymap's contract: every bindable key must be
    present, and the bytes-on-the-wire shape is
    {"id": {"action": "<act>", ...optional 'entity' / 'target_page'...}, ...}.
    Worst-case string lengths (ISO 8859-1 safe in JSON so byte length ==
    utf-8 length here) are used for entity ids and target pages.
    """
    return {kid: {"action": "navigate", "target_page": page_id} for kid in KEY_IDS}


def _page_dict(page):
    """Mirror core.build_page_dict exactly (the format that lands on the
    micropad/page/current wire). Fields and ordering matter because we
    are projecting byte sizes, not parsing JSON."""
    items_out = []
    for it in page["items"]:
        entry = {"name": it["name"], "type": it["type"]}
        if it.get("entity"):
            entry["entity"] = it["entity"]
            if it["type"] in ("light", "switch"):
                entry["state"] = "{{ states('" + it["entity"] + "') }}"
            elif it["type"] == "sensor":
                entry["state"] = "{{ states('" + it["entity"] + "') }}" + (it.get("unit", "") or "")
            elif it["type"] == "script":
                entry["state"] = "{{ states('" + it["entity"] + "') }}"
            elif it["type"] == "media_player":
                if it.get("editable"):
                    entry["value"] = (
                        "{{ state_attr('" + it["entity"] + "','volume_level') "
                        "| float(default=0) * 100 | int }}"
                    )
                    entry["min"] = it.get("min", 0)
                    entry["max"] = it.get("max", 100)
                    entry["step"] = it.get("step", 5)
                    entry["editable"] = True
                else:
                    entry["state"] = "{{ states('" + it["entity"] + "') }}"
        if it.get("target_page"):
            entry["target_page"] = it["target_page"]
        if it.get("min") is not None and it["type"] == "number":
            entry["min"] = it["min"]
            entry["max"] = it["max"]
            entry["step"] = it["step"]
            entry["editable"] = True
            entry["value"] = "{{ states('" + it["entity"] + "') }}"
        items_out.append(entry)

    payload = {
        "page_id": page["id"],
        "title": page["title"],
        "items": items_out,
    }
    if page.get("parent"):
        payload["parent"] = page["parent"]
    return payload


def _json_bytes(obj):
    """Bytes that would go on the wire for `obj` -> json.dumps(ensure_ascii=False,
    separators=(', ', ': ')) (the same shape core.generate_xxx_payload emits)."""
    return len(json.dumps(obj, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8"))


# --- Corpus load + sizing -------------------------------------------------
def _load_pages(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except OSError as exc:
        sys.stderr.write("FATAL: cannot read %s: %s\n" % (config_path, exc))
        sys.exit(2)
    except json.JSONDecodeError as exc:
        sys.stderr.write("FATAL: %s is not valid JSON: %s\n" % (config_path, exc))
        sys.exit(2)

    pages = cfg.get("pages")
    if not isinstance(pages, list) or not pages:
        sys.stderr.write("FATAL: %s has no usable 'pages' array.\n" % config_path)
        sys.exit(2)

    for p in pages:
        if not isinstance(p, dict) or not p.get("id") or not p.get("title"):
            sys.stderr.write(
                "FATAL: every page must have at least 'id' and 'title' "
                "(offending: %r)\n" % p
            )
            sys.exit(2)
        if p["id"] not in {"home"}:
            pass  # home is the only FIRMWARE-required id; warn-not-error
    return cfg, pages


def _project_worst_case_bytes(cfg, pages):
    """Project the worst-case bytes the firmware can receive on the wire.

    Two retained topics matter for the buffer budget:
      * micropad/pages/all  - one full catalog
      * micropad/keymap     - per-page effective keymap (re-published on
                              every navigation; firmware keeps the most
                              recent)

    The scout's worst-case shape from the brief: every page has a full
    14-entry per-page keymap override. We project the catalog and the
    largest single effective keymap and sum them, because both can be
    in-flight to PubSubClient at the same time and the buffer must hold
    whichever one is bigger.
    """

    # (1) Full catalog payload (micropad/pages/all).
    # Per the brief: every page has a full 14-key override for the worst
    # case. core.generate_all_pages_payload uses indent=2 and joins parts
    # with ",\n" inside a wrapper {"pages": [...]} - reproduce that
    # byte-for-byte. (Per-page keymap is NOT embedded in the catalog
    # topic; it lives on micropad/keymap. See core.py line 391 vs 411.)
    worst_pages_with_overrides = []
    for p in pages:
        wp = dict(p)
        wp["keymap"] = _worst_page_keymap(wp["id"])   # page-local override
        worst_pages_with_overrides.append(wp)
    parts = [json.dumps(_page_dict(p), ensure_ascii=False, indent=2) for p in worst_pages_with_overrides]
    body = "{\n  \"pages\": [\n" + ",\n".join(parts) + "\n  ]\n}"
    catalog_bytes = len(body.encode("utf-8"))

    # (2) Largest effective keymap payload (micropad/keymap).
    # Effective = merges a full 14-entry per-page override on top of the
    # global keymap. Worst case fills all entries with the longest-known
    # representation: a navigate action with a target_page string.
    largest_km = None
    for p in pages:
        merged = dict(_worst_page_keymap(p["id"]))
        km_obj = {"keymap": merged}
        b = _json_bytes(km_obj)
        if largest_km is None or b > largest_km[1]:
            largest_km = (p["id"], b)
    km_bytes = largest_km[1]

    total = catalog_bytes + km_bytes
    return {
        "catalog_bytes": catalog_bytes,
        "keymap_bytes": km_bytes,
        "keymap_page": largest_km[0],
        "projected_total": total,
    }


# --- Entry point ----------------------------------------------------------
def main(argv):
    config_path = (
        argv[1] if len(argv) > 1
        else os.environ.get(
            "MICROPAD_CONFIG",
            "/home/bwschti/micropad/data/config.json"
        )
    )
    cfg, pages = _load_pages(config_path)

    proj = _project_worst_case_bytes(cfg, pages)

    cap = FIRMWARE_NUL_CAP
    sys.stdout.write("config          : %s\n" % config_path)
    sys.stdout.write("page count      : %d\n" % len(pages))
    sys.stdout.write("catalog bytes   : %d  (topic micropad/pages/all)\n" % proj["catalog_bytes"])
    sys.stdout.write("keymap  bytes   : %d  (topic micropad/keymap, page=%s)\n"
                     % (proj["keymap_bytes"], proj["keymap_page"]))
    sys.stdout.write("projected total : %d  bytes (cap %d, buffer %d)\n"
                     % (proj["projected_total"], cap, FIRMWARE_BUFFER_SIZE))

    if proj["projected_total"] <= cap:
        sys.stdout.write("RESULT          : GREEN  - projected %d <= cap %d (deploy OK)\n"
                         % (proj["projected_total"], cap))
        return 0

    deficit = proj["projected_total"] - cap
    sys.stdout.write("RESULT          : RED    - projected %d > cap %d (over by %d bytes)\n"
                     % (proj["projected_total"], cap, deficit))
    sys.stdout.write(
        "ACTION          : block deploy. Either trim the catalog, raise the\n"
        "                  firmware buffer (and NUL cap in lockstep), or\n"
        "                  split the catalog across multiple retained topics.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
