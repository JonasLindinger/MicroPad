# MicroPad Developer Guide

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

How to change this project without breaking the parts that are pinned. It answers
the question the codegen cannot: *which files do I touch, and what proves it still
works?* Companion docs: `docs/firmware.md` (device), `docs/configurator.md` (web),
`docs/home-assistant.md` (broker/automation), `docs/security-release.md` (release).

## The one rule

`contracts/mqtt-contract.json` is the only source of truth for the wire protocol:
version, topics, key IDs, actions, item types, the descriptor row per item type,
payload ceilings and device caps. Everything else is either **generated** from it or
**derived at import time**:

| Consumer | How it stays in sync |
|---|---|
| `firmware/protocol_contract.h` | generated — `scripts/generate_contract.py --write` |
| `src/micropad/static/js/contracts.js` (key IDs, version) | generated, same script |
| `src/micropad/constants.py`, `ui_meta.py` | read the JSON at import |
| `firmware/micropad_core.{h,cpp}` enums + caps | asserted by tests (enum order, caps, descriptors) |

So: **edit the contract, run the generator, run the tests.** Never hand-edit
`protocol_contract.h` or the generated block in `contracts.js`.

## Recipes

### Add an action (a new keymap verb)

1. `contracts/mqtt-contract.json`: append to `actions` **at the end** (the firmware
   indexes its name table by enum value, so insertion reorders the wire vocabulary),
   and add a matching `action_meta` row (label, group, argument kind).
2. `firmware/micropad_core.h`: add the enumerator to `enum class Action` in the same
   position; `micropad_core.cpp`: add the name to `kActionNames` in the same position.
3. `python3 scripts/generate_contract.py --write`.
4. Implement the behaviour in the sketch (`applyAction`) and the Home Assistant
   template if it needs a service call.
5. Tests: extend `tests/firmware/test_core.cpp` (`testAllActionsReachDefinedOutcome`
   covers "every action has a defined outcome"), and the generator tests.
   `scripts/test_firmware_contract.sh` compares the header, the JSON and the sketch.

### Add an item type

1. Contract: append to `item_types`, add an `item_type_meta` row
   (`ha_domains`, `default_action`, `alternate_action`, `editable`, `has_value`,
   `needs_target_page`) — `alternate_action` is what a *hold* on the row dispatches
   (`none` when the type has no meaningful second action).
2. Regenerate (`--write`), then add the enumerator + name in `micropad_core.{h,cpp}`
   and one row to `ITEM_TYPE_DESCRIPTORS`
   (`{type, defaultAction, alternateAction, editable}`).
3. Nothing else: `models.py` (entity/target-page rules), `generator.py`
   (reflected/state types), `/api/meta`, the editor's validation and the keymap
   editor read the descriptor rows. A test asserts the firmware table and
   `item_type_meta` agree, so a half-done addition fails immediately.
4. Tests: `testItemTypeDescriptorTable` (host), `test_generator.py` for rendering.

### Add a topic

1. Contract `topics` — `{name, retain}`; `python3 scripts/generate_contract.py --write`.
2. Subscribe/publish it in the sketch via `mp::TOPIC_<KEY>` (never a literal: a static
   test asserts the sketch does not contain the topic strings).
3. Docs: the topic table in `docs/home-assistant.md`; the topic list in
   `tests/firmware/test_firmware_static.py` and `tests/integration/test_mqtt_contract.py`.
4. If Home Assistant must react, extend the automation template **and** the golden
   files (below).

### Add or change a page template

`src/micropad/ui_meta.py` (`page_templates()`). `/api/meta` publishes the list, so
the configurator's template picker and the API test pick it up automatically; the
browser test `tests/browser/test_items_and_entities.py::test_template_cards_create_editable_child_page`
covers the flow end to end. A template that changes a generated payload also needs
the golden update below.

### Change generated output (payloads, YAML)

The exact bytes are frozen in `tests/fixtures/golden/`. Change the generator, then

```bash
MICROPAD_UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/test_golden_output.py
git diff tests/fixtures/golden/          # review the diff: this is what gets published
```

## The gates

| Command | What it proves | Time |
|---|---|---|
| `scripts/test_firmware_contract.sh` | header↔contract↔sketch, core host tests, 140+ static contract tests | ~2 s |
| `.venv/bin/pytest tests/browser` | the real UI in Chromium against a mocked API | ~30 s |
| `.venv/bin/pytest tests/release` | manifest, notices, public-release audit | ~15 s |
| `.venv/bin/python -m pytest -q` | everything above plus backend/integration | ~70 s |
| `scripts/ci.sh` | the full stage list CI runs (adds the 3-FQBN firmware compile matrix) | minutes |
| `scripts/compile-firmware.sh --one <FQBN>` | one real firmware build + flash/RAM numbers | ~3 min |
| `python3 scripts/lint_config.py <config>` | would the pad store and show this config | <1 s |
| `python3 scripts/generate_contract.py --check` | generated sources are not stale | <1 s |

`scripts/build-simulator.sh` builds `tools/micropad_sim.cpp` (the host binary the
preview and lint use); `tests/test_simulator.py` builds it automatically.

### Pre-commit hook

```bash
git config core.hooksPath .githooks
```

Runs ruff, mypy, the contract drift gate and the firmware host tests before each
commit. It is **not** enabled automatically: enabling it changes the behaviour of
every other session and agent working in this clone.

## Conventions that are enforced by tests

- `protocol_contract.h` and the generated block in `contracts.js` must match the
  contract (`--check`; also asserted from `tests/integration/test_mqtt_contract.py`).
- Firmware `Action`/`ItemType`/`KeyId` enum order **is** the wire order.
- Firmware caps (`…_CAP`, `MAX_PAGES`, `MQTT_BUFFER_BYTES`, `LOOP_TASK_STACK_BYTES`)
  equal the contract's `caps` block, which is what the pad advertises on
  `micropad/device`.
- Display fields clip (name/title/state/unit at 32 chars); identifiers are rejected
  (`page_id`, `entity`, `target_page`). `micropad.lint` and the simulator report
  which of the two applies.
- Every file a deployment publishes must satisfy the JSON Schemas in `contracts/`.
- New files must be added to `release/software-files.txt` (bytewise sorted) — a
  release test compares that manifest with `git ls-files`.
- Every source/doc file carries the AI-assisted-development notice
  (`python3 scripts/audit_notices.py`).
- Never commit real credentials, private IPs, entity lists or keys: the
  public-release audit (`scripts/audit_public_release.py`) is the gate, and
  `release/allowed-public-values.json` lists the only approved placeholders.

## Where things live

```
contracts/          the contract + JSON Schemas (and the audit allowlist in release/)
firmware/           sketch (device I/O, WiFi, MQTT, NVS, portal) + dependency-free core
tools/              micropad_sim.cpp — the core on the host (preview and lint)
src/micropad/       Flask configurator, generator, models, lint, simulator wrapper, static UI
tests/              backend, integration, browser (playwright), firmware, release, cpp
scripts/            toolchain, compiles, gates, codegen, lint CLI, approvals
docs/               this guide, firmware, configurator, home-assistant, deployment, security
```
