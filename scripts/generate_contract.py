# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Generate the contract-derived sources from contracts/mqtt-contract.json.

The contract file is the single source of truth for the wire protocol shared by
the firmware, the config generator, the /api/meta endpoint and the browser. This
script renders the pieces that cannot read the JSON at runtime:

* ``firmware/protocol_contract.h`` — the version, topics, key ids and actions the
  sketch compiles against (``firmware/protocol_contract.h`` is generated, never
  hand-edited).
* the ``KEY_IDS`` / ``CONTRACT_VERSION`` block inside
  ``src/micropad/static/js/contracts.js`` (the browser has no filesystem access).

Everything else derives from the JSON at import time
(``src/micropad/constants.py``, ``src/micropad/ui_meta.py``), so adding an action,
an item type or a topic is a one-line change in the contract plus a regenerate.

Usage::

    python3 scripts/generate_contract.py --check   # CI/pre-commit drift gate
    python3 scripts/generate_contract.py --write   # regenerate after a contract change
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts" / "mqtt-contract.json"
HEADER_PATH = ROOT / "firmware" / "protocol_contract.h"
JS_PATH = ROOT / "src" / "micropad" / "static" / "js" / "contracts.js"

NOTICE = (
    "AI-assisted development — firmware, HA automation, config generator and docs were "
    "created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without "
    "warranty; verify on your own hardware, don't use for safety-critical applications."
)
HEADER_NOTICE = "// " + NOTICE

JS_BEGIN = "// <generated from contracts/mqtt-contract.json>"
JS_END = "// </generated from contracts/mqtt-contract.json>"


# Contract revisions this codegen has been taught. Bump the contract's `version` when the
# wire format gains or changes something the pad must understand (a new field the strict
# parser would reject, a renamed topic, a renumbered enum), then list the new revision
# here - the refusal below exists so a version bump is a conscious edit rather than a
# number change that silently generates the old shape. Older revisions stay listed as
# long as the generated output would be identical for them.
SUPPORTED_CONTRACT_VERSIONS = frozenset({2})


def load_contract() -> dict[str, Any]:
    contract: dict[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    version = contract.get("version")
    if version not in SUPPORTED_CONTRACT_VERSIONS:
        known = ", ".join(str(v) for v in sorted(SUPPORTED_CONTRACT_VERSIONS))
        raise SystemExit(
            f"unsupported contract version: {version!r} (this codegen knows: {known}); "
            "teach it the new revision in SUPPORTED_CONTRACT_VERSIONS if the change is "
            "deliberate"
        )
    return contract


def topic_constant(key: str) -> str:
    return "TOPIC_" + key.upper()


def render_header(contract: dict[str, Any]) -> str:
    topics: dict[str, dict[str, str]] = contract["topics"]
    lines: list[str] = [
        HEADER_NOTICE,
        "#pragma once",
        "#include <stddef.h>",
        "",
        "namespace mp {",
        "",
        "// GENERATED FILE — do not edit by hand. Produced by",
        "// scripts/generate_contract.py from contracts/mqtt-contract.json, the canonical",
        "// wire-contract binding shared by the firmware sketch, the config generator,",
        "// the /api/meta endpoint and the browser. Change the contract and run",
        "// `python3 scripts/generate_contract.py --write`; `--check` fails on drift.",
        f"inline constexpr unsigned CONTRACT_VERSION = {contract['version']};",
    ]
    for key in sorted(topics):  # deterministic, contract-order independent
        name = topics[key]["name"]
        lines.append(f'inline constexpr char {topic_constant(key)}[] = "{name}";')
    key_ids = cast(list[str], contract["key_ids"])
    actions = cast(list[str], contract["actions"])

    def emit_array(name: str, values: list[str]) -> None:
        lines.append(f"inline constexpr const char *{name}[] = {{")
        row = "   "
        for index, value in enumerate(values):
            entry = f' "{value}",'
            if len(row) + len(entry) > 78:
                lines.append(row)
                row = "   "
            row += entry
            if index == len(values) - 1:
                lines.append(row)
        lines.append("};")

    emit_array("KEY_IDS", key_ids)
    emit_array("ACTIONS", actions)
    lines += [
        "inline constexpr size_t KEY_ID_COUNT = sizeof(KEY_IDS) / sizeof(KEY_IDS[0]);",
        "inline constexpr size_t ACTION_COUNT = sizeof(ACTIONS) / sizeof(ACTIONS[0]);",
        "}  // namespace mp",
        "",
    ]
    return "\n".join(lines)


def render_js_block(contract: dict[str, Any]) -> str:
    key_ids = cast(list[str], contract["key_ids"])
    entries = [f"'{key}'" for key in key_ids]
    lines = [
        JS_BEGIN,
        f"export const CONTRACT_VERSION = {contract['version']};",
        "",
        "export const KEY_IDS = Object.freeze([",
    ]
    row = "  "
    for index, entry in enumerate(entries):
        piece = entry if index == len(entries) - 1 else entry + ","
        if len(row) + len(piece) > 76 and row.strip():
            lines.append(row.rstrip())
            row = "  "
        row += piece + " "
    lines.append(row.rstrip())
    lines += ["]);", JS_END]
    return "\n".join(lines)


def replace_js_block(existing: str, block: str) -> str:
    start = existing.find(JS_BEGIN)
    end = existing.find(JS_END)
    if start < 0 or end < 0 or end < start:
        raise SystemExit(f"{JS_PATH} is missing the generated block markers")
    return existing[:start] + block + existing[end + len(JS_END):]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail (exit 1) when a generated file is out of date")
    parser.add_argument("--write", action="store_true",
                        help="regenerate the generated files")
    args = parser.parse_args()
    if args.check == args.write:
        parser.error("pass exactly one of --check / --write")

    contract = load_contract()
    targets = {
        HEADER_PATH: render_header(contract),
        JS_PATH: replace_js_block(JS_PATH.read_text(encoding="utf-8"),
                                  render_js_block(contract)),
    }

    drift: list[str] = []
    for path, generated in targets.items():
        current = path.read_text(encoding="utf-8")
        if current == generated:
            continue
        if args.write:
            path.write_text(generated, encoding="utf-8")
            print(f"regenerated {path.relative_to(ROOT)}")
        else:
            drift.append(str(path.relative_to(ROOT)))

    if drift:
        print("contract drift — regenerate with scripts/generate_contract.py --write:")
        for path in drift:
            print(f"  {path}")
        return 1
    print("contract sources are in sync with contracts/mqtt-contract.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
