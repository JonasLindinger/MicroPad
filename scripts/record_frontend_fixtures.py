#!/usr/bin/env python3
# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Re-record the browser fixtures from the shipped core and the real backend.

The browser suite stands in for the server, so two recorded files have to be
refreshed whenever the contract, the firmware layout or the item descriptors move:

* ``tests/fixtures/frontend_meta.json`` — a copy of ``/api/meta``. It is a
  *snapshot*: if the descriptors/templates change and this file is not re-recorded,
  the browser tests keep passing against yesterday's API. ``tests/test_frontend_fixtures.py``
  fails when the snapshot and the live route disagree, so the drift is caught
  instead of relying on someone remembering.
* ``tests/fixtures/frontend_panel.json`` — the prim model the real firmware core
  returns for the config fixture's first page (recorded, so the browser suite draws
  a genuine model without needing the simulator binary at test time).

Run with the repo venv after a contract/layout change:

    .venv/bin/python scripts/record_frontend_fixtures.py

then review the diff of ``tests/fixtures/`` before committing it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from micropad import simulator  # noqa: E402
from micropad.app import create_app  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def _is_leaf(value: object) -> bool:
    """True for a mapping whose values are all scalars (printed on one line)."""
    return isinstance(value, dict) and all(
        not isinstance(item, (dict, list)) for item in value.values()
    )


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def dump_fixture(value: object, indent: int = 0) -> str:
    """Serialize like the existing recorded fixtures: one entity per line.

    Objects are expanded one key per line, but a mapping of plain scalars (an
    action descriptor, a key binding, a page item) stays on a single line and a
    list of them becomes one line per entry. That keeps a 900-line metadata
    recording readable *and* keeps a regeneration diff limited to what actually
    changed, instead of reformatting every nested object.
    """
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        if _is_leaf(value):
            return _compact(value)
        entries = [
            f'{pad}  {json.dumps(key, ensure_ascii=False)}: {dump_fixture(item, indent + 1)}'
            for key, item in value.items()
        ]
        return "{\n" + ",\n".join(entries) + f"\n{pad}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        entries = [f"{pad}  {dump_fixture(item, indent + 1)}" for item in value]
        return "[\n" + ",\n".join(entries) + f"\n{pad}]"
    return json.dumps(value, ensure_ascii=False)


def _notice() -> str:
    from micropad.constants import AI_NOTICE

    return AI_NOTICE


def record_panel() -> None:
    config = json.loads((FIXTURES / "frontend_config.json").read_text(encoding="utf-8"))
    page = config["pages"][0]
    response = simulator.simulate(
        simulator.page_directives(page, selected=0, network=4, usb_host=True, press="r0c3")
    )
    payload = {
        "_ai_assisted_notice": _notice(),
        "page_id": page["page_id"],
        "response": response,
    }
    (FIXTURES / "frontend_panel.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"recorded frontend_panel.json ({len(response['prims'])} prims)")


def record_meta() -> None:
    live = create_app().test_client().get("/api/meta").get_json()
    payload = {"_ai_assisted_notice": _notice(), **live}
    (FIXTURES / "frontend_meta.json").write_text(
        dump_fixture(payload) + "\n", encoding="utf-8"
    )
    print("recorded frontend_meta.json from /api/meta")


def ensure_control_fields() -> None:
    """Every item in the config fixture carries the control field.

    The editor reads it, and a fixture without it would test a shape the backend
    never emits.
    """
    path = FIXTURES / "frontend_config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for page in config.get("pages", []):
        for item in page.get("items", []):
            if "control" not in item:
                item["control"] = "button"
                changed = True
    if changed:
        path.write_text(dump_fixture(config) + "\n", encoding="utf-8")
        print("added control fields to frontend_config.json")


if __name__ == "__main__":
    ensure_control_fields()
    record_panel()
    record_meta()
