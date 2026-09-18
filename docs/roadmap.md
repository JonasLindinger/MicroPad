# MicroPad — feature roadmap / backlog (2026-09-15)

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

Working document for choosing the next work packages. Every item carries its effort, risk and
whether it can be validated without the physical pad; "done" means the definition of done at the top
of each phase was actually met, not that code exists.

**Status: Phase A complete (A1–A9); B1–B5 and B7 shipped; C1 (long press + double press),
C2 (diagnostics), C4 (cover, fan, input_boolean, lock) and C5 (slider/dimmer interaction —
per-row `control`, the `adjust` action and the checkbox value column) shipped.** See
`docs/status.md` for the current ship list and how each item was verified. Next slice: B8
(post-upload read-back), C6 (icon set), C7 (portal factory reset) — or the items marked
"blocked" once their open decision is made.

**How to read it**
* **Effort:** S ≤ 2 h · M ≈ half a day · L ≈ 1–2 days · XL = 3+ days (incl. tests/docs).
* **Risk:** low = host-testable, no hardware · med = touches a pinned contract or the core data model ·
  high = only verifiable on the physical pad.
* **HW** = needs the real MicroPad (or someone pressing keys on it) before it counts as done.
* **DoD** = definition of done; every item ends in the existing gates: host core binary +
  `test_firmware_static.py` + `scripts/test_firmware_contract.sh` + the 3-FQBN compile matrix
  (+ browser/backend suites for UI work), no push without approval.

Current baseline for reference: flash 1 006 747 B (76 %), static RAM 173 364 B (52 %), free DRAM
154 316 B, live catalog 118 344 B, callback frame 6 704 B, loop stack 16 KiB.
Measured with a real `arduino-cli` build of the pinned hwcdc/PSRAM FQBN: A1–A3 +1 004 B flash /
0 B RAM, A4's shared snapshot builder +36 B, C2's diagnostics +768 B flash / +40 B RAM, C1's long
press +184 B flash / +48 B RAM. Flash is not the constraint: 23 % of the partition (302 973 B) is
free.)

---

## Shipped (2026-09-15)

**A1 — contract is the single source of truth.** `contracts/mqtt-contract.json` gained
`action_meta`, `item_type_meta`, `caps` and the sixth topic; `scripts/generate_contract.py`
(`--write` / `--check`) renders `firmware/protocol_contract.h` and the browser's key-ID/version
block, and `micropad.constants` / `micropad.ui_meta` derive from the JSON at import time.
Drift gates: `scripts/test_firmware_contract.sh`, the new
`tests/integration/test_mqtt_contract.py` block (generated sources, enum order, firmware caps,
descriptor table, backend item rules, `/api/meta`), and `tests/test_frontend_delivery.py` (browser
fixture vs live meta). The codegen `--check` runs inside the `mqtt-contract` CI stage, so no new CI
stage (the stage list is a pinned release contract) was needed.
*Deviation from the plan:* Python is derived at import (no generated file) — one less artefact to
keep in sync; the C++ header and the JS block still need codegen because they cannot read JSON.

**A2 — one descriptor row per item type.** `ItemTypeDescriptor` + `ITEM_TYPE_DESCRIPTORS` in
`micropad_core.h/.cpp` (`{type, defaultAction, editable}`) now feeds `actionForSelectedItem`; the
contract's `item_type_meta` feeds `models.py` (entity/target-page rules), `generator.py`
(reflected/state-column types), `/api/meta` and the editor's `draftErrors`. Magic table sizes
(`kActionNames[21]`, `kItemTypeNames[11]`, the loop-stack literal, `items max_length=20`) replaced
by enum-bounded/contract constants. Room for the item-type row: this is the baseline for C4.
*Deviation:* the firmware row carries only what the firmware uses (`defaultAction`, `editable`);
HA domains stay in `item_type_meta`, and a test asserts the two agree.

**A3 — the pad advertises its own limits.** Every connection publishes the retained
`micropad/device` payload (`fw`, `contract`, `caps` — all read from the firmware's own constants,
`…_CAP - 1` for the string fields). `/api/meta` now exposes `limits`, `caps` and `item_type_meta`
so the configurator can warn before the pad clips a value or drops a catalog.
*Deviation:* the topic is `micropad/device` (a sixth contract topic) instead of `micropad/hello`;
the payload is flat `caps` rather than top-level fields. Backend-side *enforcement* (refusing to
publish) is still A6/B3 — today the data is exposed, the warning UI is not built yet.

**Still to do from this slice:** A6's config-lint gate and B2/B3 (budget meters + callouts) are the
natural follow-ups that consume A3's caps.

**A4 — firmware-in-the-loop core.** `tools/micropad_sim.cpp` (+ `scripts/build-simulator.sh`,
plain g++) compiles the shipped core into a host binary that answers "what does the panel render
for this page / what does this key do" in one JSON object: the row window, the prim list from
`layoutNormalUi`/`layoutPortalUi`, `would_reject` + `findings` for clipped or refused fields, and
the resolved binding/item action for a pressed key. The sketch and the simulator now share one
snapshot builder (`fillPageSnapshot()` moved from the sketch into the core), so the row window
cannot differ between panel and preview. Cost: +36 B flash, 0 B RAM.
*Honest limitation:* the JSON→struct decoding still lives in the sketch (ArduinoJson), so the
simulator reads a tiny documented directive format instead. Moving the payload decoder into the
core remains a named follow-up (it would also remove ArduinoJson from the hot path) — see B11 below.

**A5 — `/api/simulate`.** POST a page (draft accepted) + pad state, get the core's model back.
`src/micropad/simulator.py` owns the wrapper; a missing binary answers 503 `simulator_unavailable`
and rejected input answers 400 with the offending field, so the UI never shows an invented panel.

**B1/B2/B3 — the website.** `static/js/panel-preview.js` draws the prim model as SVG at hardware
resolution (296x128, monochrome, FreeMono) with a "show setup portal" toggle; it contains no layout
knowledge — the geometry is whatever the firmware returns. `static/js/analysis-panel.js` renders the
budget meters (page/catalog/MQTT buffer/pages/items) and the findings list from `/api/lint`
(`src/micropad/lint.py`), which combines the contract caps with the real generator's byte sizes and
reports `field_clipped` (warning) vs `identifier_too_long` (blocks publish) with a `where` path.
Tests: `tests/test_simulator.py` (12, including a gate that the recorded browser fixture still
matches the current core), `tests/browser/test_preview_and_budgets.py` (3, drawing a recorded
real-core model), plus a browser-fixture-vs-`/api/meta` drift test.

**A6 — the same analysis without the browser.** `scripts/lint_config.py` exposes `micropad.lint`
on the command line (exit 0 publishable / 1 findings / 2 unreadable, `--strict`, `--json`), and the
`firmware-host` CI stage now also builds and runs `tests/firmware/test_core.cpp` and
`tests/firmware/test_firmware_static.py`. Those two ran *only* on a developer machine before: the
stage called `scripts/test_firmware_contract.sh`, which covered the header/contract checks alone, so
a core regression could reach review unnoticed. The stage takes ~1 s longer.

**C1 (long press) — the first gesture.** A hold on the row under the cursor dispatches that item
type's **alternate action** (light/switch: turn *off* instead of toggling); every other type ignores
the hold. Deliberately storage-free: the alternate comes from a new `alternate_action` column in
`item_type_meta`, so no per-binding gesture fields were added (that would have cost ~1.8 KB of RAM
and a keymap-schema change for the same user-visible behaviour). The core owns the timing
(`LONG_PRESS_MS` = 600 ms from the *debounced* press, emitted once per hold as `Edge::LongPress`),
the dispatch reuses the ordinary action path — so the MQTT event, the HA automation and the payload
schemas are untouched. The wake press is exempt: holding the key that woke the pad never fires the
gesture (a host test caught that case). Cost: +184 B flash, +48 B RAM.
*Closed from C1 (2026-09-18):* **double press** — and with it the per-binding gesture fields C1
deferred, now that a second gesture (hold) needs a target of its own: a binding carries `hold` and
`double`, each a full action/entity/target-page target, and a key with a double gesture defers its
tap for `DOUBLE_PRESS_MS` (350 ms) so a double press fires exactly once. *Still open from C1:*
**encoder acceleration** (a "repeat N" multiplier while the knob keeps turning) — it needs a
parameter, not a binding, and nothing else depends on it.

**C5 (slider interaction) — the encoder adjusts the row under the cursor.** Shipped 2026-09-18
together with the checkbox value column and the gesture bindings above; the design decisions and
the three-way encoder semantics (step / scroll / no-op) are recorded in `docs/firmware.md`
("Slider rows") and `docs/configurator.md` ("How a row is driven"). C5's original framing —
"`edit` on a light steps brightness via the encoder" — turned out to be the wrong shape: pressing
Enter first is exactly the interaction the feature was asked to remove, and a slider row needs no
edit mode at all. The `edit`/`confirm` path for editable numbers is unchanged and still the way to
type a bounded value. Two decisions were settled by the operator rather than by the code: the
encoder's default stays `scroll_up`/`scroll_down` (`adjust` is an option in the key map, not a new
behaviour on every existing page), and a slider at its bound does nothing at all instead of
scrolling on — the escape off such a row is any key bound to scrolling, which is what the default
does.

**C2 — diagnostics.** The pad publishes the retained `micropad/diag` payload on every connect and
then once a minute while connected: uptime, MQTT connects, catalog/page/keymap parse accept+reject
counters, dropped events (queue full) and the heap high-water mark. `Diagnostics` +
`formatDiagnostics()` live in the core (host-tested byte for byte, incl. the worst-case length), the
sketch only bumps counters and publishes from a 256-byte stack buffer — no ArduinoJson document, so
the format is testable on the host and costs no heap. Cost: +768 B flash, +40 B RAM. The topic was
added by editing the contract and running the codegen — the A1 foundation doing its job.

**A7 — golden files.** `tests/test_golden_output.py` freezes the automation YAML and the three
published payloads for `tests/fixtures/golden_config.json` (3 pages, category with target page,
editable number, scene, per-page keymap override) into `tests/fixtures/golden/` as real files, so an
intended change produces a reviewable diff. Regenerate with `MICROPAD_UPDATE_GOLDEN=1`. The unit
tests assert properties, so a generator refactor could change published bytes silently before this.
Also verified negatively: mutating one golden key fails two tests.
*Note:* the captured artifacts are exempt from the "every file carries the AI notice" rule (same
reasoning as the machine-generated lock file) — a notice field would change the payload under test.

**A8 — pre-commit gate.** `.githooks/pre-commit` runs ruff, mypy, the contract drift check and the
firmware host checks (~4 s). Deliberately not enabled automatically: the hook changes the behaviour
of every other session/agent in the clone, so `git config core.hooksPath .githooks` is an explicit
operator decision (documented in `docs/developer.md`).

**A9 — developer guide.** `docs/developer.md`: recipes the codegen cannot express (add an action /
item type / topic / page template), the generated-vs-derived table, the gate table with timings, the
golden regeneration workflow, and the conventions tests enforce (enum order = wire order, caps mirror
the contract, clipped vs rejected fields, manifest completeness, the AI notice, the no-secrets gate).

**B5 — page generation from the entity cache.** `/api/entity-groups` reports the cached domains with
counts and their item type (from `item_type_meta`); `/api/build-pages` returns page drafts — one page
per domain, split at 20 entities, stopping at the 24-page ceiling, `number` items carrying the
entity's real min/max/step, names/states/units clipped to the firmware caps with every shortening
reported in `notes`, and non-pageable domains listed in `skipped`. The UI (`page-generator.js`) adds
one button per domain plus "all pageable domains" and a cache-refresh button; drafts are appended to
the open configuration, which autosaves like any other edit. Room grouping is deliberately not
offered: `/api/states` carries no area information, so "one page per room" would need the area
registry (WebSocket API) — a separate decision (B12 below).

**B12 (new)** — area/room grouping for page generation: needs the HA area registry (WebSocket API or
`/api/config/area_registry/list` with a long-lived token), i.e. a new credential/API surface.
Effort M, risk med (new HA API surface), decision needed.

**B11 (new)** — move the payload decoding into the core so the sketch and the simulator share the
JSON→struct step as well (removes the last mirrored logic and ArduinoJson from the hot path).
Effort M/L, risk med (device-facing path), needs a hardware pass.

---

## Phase A — make "adding a feature" cheap (foundations, no hardware)

| ID | Item | Why | Effort | Risk |
|---|---|---|---|---|
| **A1** | **Contract codegen** — `scripts/generate_contract.py` emits `protocol_contract.h`, the Python constants and `static/js/contracts.js` from one source (`contracts/mqtt-contract.json`), plus a CI drift check | Actions/item types/key ids are hand-maintained in 5 places today; this is the single biggest drag on new features | M | low |
| **A2** | **Item-type descriptor table in the core** — one host-tested row per type: `{ha_domain, default_action, editable, has_value, needs_target_page, unit_hint}`; `actionForSelectedItem`, the generator and the UI read the same table | A new item type becomes one row instead of edits in 6 files | M | med |
| **A3** | **Firmware capability handshake** — pad publishes retained `micropad/hello` (`fw_version`, `contract_version`, `mqtt_buffer_bytes`, field caps, `max_pages`, `max_items`) | Lets the backend refuse a config the device cannot parse and lets the UI say "firmware too old"; closes the silent-drop class we just fixed | M | low |
| **A4** | **Firmware-in-the-loop core CLI** — compile `micropad_core.cpp` into a small host binary (`parse page/keymap JSON → apply key event → emit prim model`), used by tests *and* by the configurator | Preview, lint and simulate all run the **real** firmware logic instead of a re-implementation | M | low |
| **A5** | **`/api/simulate`** — feed a page payload + a key event, return the prim model, the resulting event queue and whether the payload would be clipped/rejected | Turns "does this config work on the device?" into a button; basis for the preview in B1 | S–M | low |
| **A6** | **Config lint CLI + CI** — `scripts/lint_config.py config.json` = validate → generate → byte budgets → real-core parse → exit non-zero with the reason; add it (and the host core/static tests) to CI plus a **contract-drift gate** | Catches an unpublishable config before it reaches the broker | S | low |
| **A7** | **Golden-file tests for generated output** — snapshot the generated automation YAML + retained payloads per example config | A generator refactor can currently change published payloads silently | S | low |
| **A8** | **Pre-commit hook** (ruff, mypy, host core, static firmware tests) | Cheap to add, removes the "which suite do I run" friction | S | low |
| **A9** | **Developer guide "add an action / item type / page template"** (`docs/developer.md`) with the checklist the codegen can't cover | Makes the process explicit for future sessions | S | low |

**Suggested first slice: A1 + A2 + A3** (one coherent "contract is the source of truth" step).

## Phase B — config website (no hardware needed)

| ID | Item | Why | Effort | Risk |
|---|---|---|---|---|
| **B1** | **Faithful panel preview** (SVG from the prim model: 296×128, FreeMono metrics, title clip 22, value column 12, cursor, scrollbar, portal view) | "What will the panel look like" is asked every single time; there is no preview at all today | M | low |
| **B2** | **Budget meters + limits in `/api/meta`** (page 8192, catalog 16000 vs 16384 buffer, items ≤ 20, pages ≤ 24, field caps name/title/state 32, entity 96) | The UI currently cannot warn about clipping or an oversized catalog | S | low |
| **B3** | **"What the device changes" callout** (clipped values, dropped pages, entity mismatch) on top of A5 | Makes the silent behaviour of the firmware visible while editing | S | low |
| **B4** | **Hardware-shaped keymap editor** — clickable 3×4 matrix + encoder wheel, inherited-vs-overridden per page, drag action chips | Keymaps are edited as text rows today; the physical layout is the mental model | M | low |
| **B5** | **Entity-first page generation** — pick areas/domains/favourites → generated pages ("one page per room", lights/scenes/media/Spotiify) on top of the template picker | Adding a page becomes a 3-click operation | M | low |
| **B6** | **Live device mirror + remote key injection** — backend subscribes to `micropad/event`/`power`, UI shows the pad state and adds "press r0c3" buttons | Test a keymap without touching the pad; would have made the last debugging session much faster | M | low |
| **B7** | **Config history / undo / diff-before-apply** — versioned snapshots (the approval-hash flow already exists) + one-click rollback | Recover from a bad config in seconds | M | low |
| **B8** | **Apply read-back verification** — after upload, re-read the retained topics from the broker and compare with the generated payloads (`scripts/verify_live_mqtt.py` is the basis) | Proves the broker really holds what was published | S–M | low |
| **B9** | **UI polish**: German/English switch, dark mode, keyboard navigation, mobile layout | The UI is English-only and desktop-ish | S–M | low |
| **B10** | **Diagnostics tab** — render A3/C2 data: firmware version, uptime, heap, reconnect count, rejected payloads, last error | Field debugging without a serial console | S | low |

## Phase C — device features (firmware/HA, host-testable)

| ID | Item | Why | Effort | HW |
|---|---|---|---|---|
| **C1** | **Gesture layer in the core**: long-press, double-press, hold-to-repeat, encoder acceleration | 14 inputs → ~40 actions with no hardware change; purely host-testable | M | – |
| **C2** | **Diagnostics topic** (`micropad/diag`, retained): uptime, min free heap, MQTT reconnects, parse rejections, stack high-water mark, queue overflow | We just removed a lot of silence; this makes the next issue visible | S–M | – |
| **C3** | **Notification service** — HA publishes `micropad/notify`, the pad shows a banner row until dismissed (any key) | Turns the pad into an output device, not just an input | M | – |
| **C4** | **New item types**: `cover` (blinds), `climate` (setpoint), `select`/`input_select` (encoder cycles options), `fan`, `lock`, `input_boolean` | Depends on A2: each is one descriptor row + generator mapping + UI entry | M per 2–3 types | – |
| **C5** | **Dimmer interaction** — `edit` on a light steps brightness (5 % steps via the encoder), optimistic state | The encoder currently only scrolls; a dimmer is the most-requested missing interaction | M | – |
| **C6** | **Icon/glyph set** in the prim model (wifi-error, lock, play/pause, thermometer, battery, brightness) | Cheap (~200 B flash), lifts the UI a lot | S | – |
| **C7** | **Portal: factory reset + connection result + OTA upload** — "forget settings / erase NVS", show the Wi-Fi/MQTT result on the page after saving, and accept a firmware `.bin` behind the portal (`Update.h`) | No more USB-tethering, and no bricked-feeling device after a bad save | M (reset/test) / L (OTA) | OTA yes |
| **C8** | **Per-page refresh policy** — optional periodic partial refresh for sensor/dashboard pages (e.g. every 5 min) + a dashboard page template | Sensor values are static until the backend republishes; a wall display wants fresh numbers | M | – |
| **C9** | **Elapsed/back-stack indicator** in the title strip (breadcrumb depth) + long-press context menu on a row (toggle/pin/edit) | Better orientation in deep page trees | S–M | – |
| **C10** | **Multi-device support** — per-device pages/keymaps (client ids already derive from the MAC) + a device picker in the UI | Two pads, different keymaps, one configurator | L | – |

## Phase D — hardware-dependent (needs the pad + a decision)

| ID | Item | Note | Effort |
|---|---|---|---|
| **D1** | **Battery gauge** — ADC divider → battery % in the retained `micropad/power` payload, low-battery icon | Needs a PCB revision (Rev C) before it can be built | M + PCB |
| **D2** | **Wall-dashboard mode** — timer wake → refresh a dashboard page → deep sleep, with a battery/no-USB profile | The sleep path and the render mailbox already exist; battery life depends on the panel refresh cost | L |
| **D3** | **Encoder push button** (if the part has one) / additional inputs | Verify the PCB and the encoder datasheet first | S–M + HW |

## Phase E — deliberately not doing (keeps the project clean)

* A second protocol version or a compatibility matrix beyond A3's handshake.
* Any cloud dependency; the pad talks to its own broker and nothing else.
* A scripting/template engine inside the firmware (RAM-bound: live catalog is already 118 KB).
* Bigger event queue/buffers — the contract limits are pinned and the RAM cost is real.
* OTA *without* the portal (an unauthenticated update path is a security regression).

## Mapping to the earlier chat list (nothing lost)

* "One source of truth + codegen" → **A1** · "Item-type descriptor table" → **A2** ·
  "Capability handshake" → **A3** · "Firmware-in-the-loop preview" → **A4/A5**.
* "Faithful panel preview" → **B1** · "Budget meters" → **B2/B3** · "Hardware-shaped keymap editor" → **B4** ·
  "Entity-first authoring" → **B5** · "Live device mirror + key injection" → **B6** ·
  "Config history/undo/diff" → **B7**.
* "Gestures" → **C1** · "OTA via portal" → **C7** · "Diagnostics topic" → **C2** ·
  "Battery gauge" → **D1** · "Icon/glyph table" → **C6** · "Multi-device" → **C10**.

## Proposed order (my recommendation)

1. **A1 + A2 + A3** — contract single-source + handshake (unblocks everything else).
2. **A4 + A5 + B1 + B2 + B3** — real-core preview + budget meters on the website.
3. **C1 + C2** — gestures and diagnostics (device value, host-testable).
4. **B4 + B5 + B6** — keymap editor, page generation, live mirror.
5. **C7 (portal)** — factory reset + OTA.
6. Everything else as it fits; **D*** only with hardware on the desk.

Pick the items (or a different order) and I start with the first two or three.
