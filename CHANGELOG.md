# Changelog

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

Two numbers describe a build. The **firmware version** is what the pad publishes on
`micropad/device` and what the configurator shows; the **contract revision** is the wire
format both sides speak. The clean-room line was merged into `main` through PR #9 and is
released from there; the entries below are grouped by the release they shipped in.

## [Unreleased]

### Added

- **Slider rows: the encoder adjusts the row under the cursor, without an Enter
  press.** An item's `control` is now `button` (unchanged) or `slider`, and the
  editor offers the slider only for the types that carry an adjustable value
  (light, fan, cover, media_player, number — the contract's new `adjustable`
  flag). The new `adjust` action steps that row by its own `step`, clamped into
  `[min, max]`, and publishes `{"action":"adjust","entity":…,"value":<new>}`;
  Home Assistant writes the value back through the attribute that means "level"
  for the domain (`brightness_pct`, `percentage`, `position`, `volume_level`,
  `number.set_value`), gated by the row's own range so a malformed event cannot
  write an out-of-range value. Three documented properties decide what a turn does:
  a row that is *not* a slider scrolls, a slider already at its bound in that
  direction does **nothing at all** (no step, no event, no cursor move: the value is
  at its limit and the limit holds), and a matrix key — which has no direction — is a
  defined no-op. **The encoder's shipped default stays `scroll_up`/`scroll_down`**:
  `adjust` is an option in the key map, not a new behaviour imposed on every existing
  page. The way off a slider parked at its bound is therefore any key bound to
  scrolling (or `back`/`home`) — including the encoder itself, which is what its
  default does.
- **The pad draws a checkbox instead of the word `on`/`off`.** Two-valued states
  render as a box with a tick in the value column, and the vocabulary is the
  checkbox's own rather than one entity type's: `on`/`off`, `true`/`false`,
  `open`/`closed`, `locked`/`unlocked`, `home`/`away`, `yes`/`no`,
  `active`/`inactive`, `enabled`/`disabled`, case-insensitively — so a cover or a
  lock reads at a glance like a switch does. The checkbox is a *symbol*, never text:
  a word in the value column is what this replaces. States that only *look* binary (a
  media player's `playing`/`paused`, a sensor reading, an empty state) keep their text,
  because a tick would claim a semantics the entity does not have. Composed from the
  existing rect/line primitives, so the panel translator and the browser preview needed
  no new primitive kind. The item editor offers the matching switch next to the state
  field — only for that vocabulary, so it can never turn a reading like `23.4` into a
  boolean — and toggling it writes the row's **own pair** (`open` → `closed`), not
  `on`/`off`, which would be a state Home Assistant contradicts on the next refresh.
- **Hold and double press are configurable per key, in the keymap and in the
  firmware.** A binding carries an optional `hold` and an optional `double`
  target, each with its own action, entity and target page ("hold turns *this
  other* light off"). An unset gesture is omitted from the published keymap and
  behaves exactly as before, so an older keymap and an older configuration stay
  valid. The core emits `Edge::DoublePress` on a second debounced press inside
  `DOUBLE_PRESS_MS` (350 ms); only a key that actually binds a double gesture
  defers its tap for that window, so every other key keeps the zero-latency press
  edge. The keymap editor gained one block per gesture plus a badge on the key
  face, and `scripts/lint_config.py` now reports nine new findings: four *errors*
  for a slider row that cannot work (unknown control, a type without an adjustable
  value, no usable range, no step) and five *warnings* for a binding that can never
  fire (`adjust` on a key instead of the encoder, a gesture on the encoder, a
  directional action inside a gesture, a gesture with an entity but no action, a
  gesture without the entity its action needs).
- **Contract revision 3** carries the three additions (`adjust`, `control`,
  `hold`/`double`, plus `controls`/`control_meta`/`gestures`/`gesture_meta`,
  the `adjustable` and `hold_ms`/`double_press_ms` capabilities, and the page
  schema's `control` field). `firmware/protocol_contract.h` and the browser
  block are regenerated; the codegen refuses the old revision on purpose.
- **The recorded frontend fixtures are a gate instead of a guess.**
  `scripts/record_frontend_fixtures.py` is tracked now (it re-records the browser
  suite's `/api/meta` snapshot and the panel prim model from the real backend and
  the real core), and `tests/test_frontend_fixtures.py` fails when the snapshot and
  the live route disagree. That closes a real gap: the browser suite had been green
  against a `/api/meta` snapshot recorded *before* the template item names were
  renamed, so a browser test asserted names the API no longer produced. Re-recording
  the fixture exposed it; the gate makes the next one fail loudly instead.

### Changed

- **`FIRMWARE_VERSION` 1.2.0 → 1.3.0.** The wire contract moved to revision 3, so the
  version the pad reports on `micropad/device` moves with it: a config that uses row
  controls or gestures is only valid for this build, and the retained device payload is
  how a backend or a dashboard can tell which build is on the panel. (`pyproject.toml`
  still says `0.1.0` and the last release tag is `v1`; aligning those is the release
  step, not this change.)
- **A `Binding` grew from 131 B to 393 B**, because a gesture target is the same
  shape as a binding: the key's own `tap` target plus the optional `hold` and
  `double` ones. Measured with the same compiler on both revisions
  (`build/analysis/size_probe*.cpp`): `sizeof(BindingTarget)` 131 B,
  `sizeof(Binding)` 393 B, `PadController` 118 360 → 122 024 B — i.e. the live
  keymap (`activeKeymap[14]`) alone costs **+3 664 B of RAM**. That is the price of
  the feature and there is no cheaper way to store two extra action/entity targets
  per key; the alternative (a second, gesture-only table) would have needed a
  private action table, which the static tests refuse by design.
- **The keymap parse buffer lives in `.bss`, not on the loop-task stack.** The
  sketch owns `micropad::Binding keymapStaging[KEY_COUNT]` (**+5 502 B of `.bss`**)
  and `parseEffectiveKeymap()` fills the caller's buffer instead of building a
  temporary: with 393-B bindings, *two* such arrays inside the MQTT callback frame
  would have taken ~11 KB of the loop task's 16 KiB stack, so the parse path would
  have risked a stack overflow exactly while decoding a keymap payload. The buffer
  is static because the callback runs on that one task — a `.bss` array is the
  correct home for it.
- **`RENDER_PRIM_CAP` 32 → 40.** A full four-row page measured 22 prims with text
  rows and 30 with four slider rows (track, fill, value) or four ticked checkbox
  rows; 40 keeps the documented 25 % headroom over the worst case instead of the
  one prim 32 would have left. The render buffer is 50 B per prim, so this is
  +400 B.
- **`DebouncedInput` 12 → 24 B and `RenderRow` 92 → 100 B** (the release timestamp
  and the double-press flag; the row's `min`/`max`). `sizeof(Item)` is unchanged at
  232 B — the `control` byte fits in existing padding, so a packed catalog costs no
  more than before.
- **Net firmware cost of the whole slice, measured with `arduino-cli` on the pinned
  `hwcdc/PSRAM` FQBN (baseline built from the previous revision with the same
  command): flash 995 247 B (75 %) → 997 439 B (76 %)** — +2 192 B — and **static
  RAM 173 244 B (52 %) → 182 620 B (55 %)**, +9 376 B, which is the two arrays above
  (3 664 + 5 502 B) plus the wider input/row structures and the bigger prim buffer.
  145 060 B of RAM stay free, and the 16 KiB loop stack keeps its margin because the
  keymap parse no longer happens on it.

### Fixed

- **A powered pad is no longer treated as battery.** `isPowered()` used
  `static_cast<bool>(Serial)` only, which in the hwcdc build is
  `HWCDC::isCDC_Connected()`: its session flag is set by the HWCDC interrupt
  handler, and that handler is installed **only** by `HWCDC::begin()`. The sketch
  never called it (the core calls `Serial.begin()` automatically for the TinyUSB
  build only, `cores/esp32/main.cpp`: `#if ARDUINO_USB_CDC_ON_BOOT && !ARDUINO_USB_MODE`),
  so `isPowered()` was permanently `false`: the pad counted as battery-powered on
  every cable, slept after 60 s idle, stopped pumping `mqttClient.loop()`, lost the
  15 s keepalive, and the broker dropped it. Detection now uses the USB-serial-JTAG
  host check (`Serial.isPlugged()`, the IDF SOF watchdog) before the session flag —
  the v6 semantics, restored — and `configureCdc()` starts the HWCDC driver. The
  TinyUSB build uses the v6 `tud_cdc_n_connected(0)` arm, and both sleep guards
  additionally honour the debounced `stableUsbHost` state.
- **Repository cleanup:** the `.arduino` toolchain symlink and the
  `src/micropad_configurator.egg-info/` build artifacts that an accidental commit
  added to the tree are removed again, and `.gitignore` ignores both (the
  `*.egg-info/` line had been merged into `*.egg-info/.arduino`). The committed
  symlink pointed at a local `/tmp` toolchain tree, which made the firmware CI job
  fail at the toolchain install step.

## [1.2.0] — 2026-09-15

First release of the clean-room line: 13 commits, and every claim here was measured, not
assumed.

### Added

- **Four item types** — `cover`, `fan`, `input_boolean`, `lock`, each one contract row plus
  one firmware descriptor row plus the Home Assistant service mapping.
- **Long press** — holding Enter dispatches an item's *second* action (a light turns off, a
  lock unlocks), resolved through the same descriptor table as a tap.
- **`micropad/diag`** — retained diagnostics: uptime, MQTT connects, accept/reject counters
  per payload type, dropped events, the heap high-water mark and `stack_min`, the loop task's
  stack high-water mark, so the 16 KiB stack is confirmed by reading a topic rather than by
  attaching a debugger. Formatted in the core with a
  256-byte stack buffer, no ArduinoJson.
- **`micropad/device`** — the pad advertises its firmware version, contract revision and
  field caps, so the configurator can refuse a configuration the device cannot parse
  instead of watching it disappear.
- **Firmware-in-the-loop preview** — `tools/micropad_sim.cpp` compiles the shipped core into
  a host binary; the configurator's panel preview (`/api/simulate`) and the browser SVG are
  drawn from that model, so the preview cannot disagree with the device's layout code.
- **Config lint** — `scripts/lint_config.py` (exit 0/1/2, `--strict`, `--json`) and
  `POST /api/lint`, with budget meters for page bytes, catalog bytes, pages, items and
  per-field length against the firmware caps.
- **Config history** — every save leaves a content-addressed revision beside the config
  file (mode 600, git-ignored); `GET /api/history`, a structural diff that never prints a
  secret, and a restore behind a confirmation dialog.
- **Page generation from the entity cache** — `/api/entity-groups` and `/api/build-pages`
  turn Home Assistant domains into pages, reporting every shortened name and every skipped
  domain instead of silently trimming.
- **Single source of truth** — `contracts/mqtt-contract.json` drives `protocol_contract.h`,
  the browser constants and the Python constants; `scripts/generate_contract.py --write/--check`
  plus drift tests keep them from diverging.
- **Developer documentation** — `docs/developer.md` (how to add an action, an item type, a
  topic or a template), `docs/roadmap.md` (the 33-item backlog) and `docs/status.md` (what is
  shipped, with its evidence).

### Changed

- **Contract revision 1 → 2.** The wire format gained two topics (`micropad/device`,
  `micropad/diag`), four item types and the `alternate_action` binding field. A
  contract-2 configuration must not be pushed to a pad that reports revision 1 on
  `micropad/device`; the configurator warns on the mismatch. The codegen refuses a
  revision it has not been taught, so a future bump is a conscious edit.
- **Display strings are clipped, identifiers are not.** A 40-character name is shortened to
  fit the panel; a too-long entity id is still rejected, because silently trimming one
  would address a different device.
- Display caps 49 → 33 bytes and the render prim budget 48 → 32 (still > 25 % headroom over
  the measured worst case).

### Fixed

- **Silent failure paths** (the 1.2.0 firmware set): the MQTT payload is parsed in place
  (the 16 385-byte stack copy is gone); a failed `subscribe`, `publish` or watchdog
  subscription is treated as a lost connection instead of being ignored; an NVS save
  invalidates its commit marker first, so an interrupted write cannot boot with a mixture
  of old and new credentials; an unreachable Wi-Fi record re-opens the setup portal
  instead of stranding the device; a missing retained catalog is re-requested every 60 s.
- **A configured pad re-entered the setup portal on every boot.** The NVS read helpers used
  `Preferences::getBytesLength()`, a *blob* accessor (`nvs_get_blob`) that fails with
  `ESP_ERR_NVS_TYPE_MISMATCH` on an entry written with `nvs_set_str` and therefore reports 0 for a
  value that is present. Every stored string looked absent, so `loadSettings()` rejected the
  complete record and the pad fell back to the portal - which reads exactly like a Wi-Fi problem.
  Presence and values now use the string-shaped APIs, the port uses the type-aware `getType()`,
  and a static test keeps the blob accessor out of the sketch. A refused save now names the field
  and the rule instead of answering "invalid settings".
- **The firmware CI stage silently skipped four contract classes.** `unittest.main()` sat
  above them in `tests/firmware/test_firmware_static.py`, so the stage reported green with
  142 cases while 190 were collected — including every check for the fixes above. The entry
  point now sits at the end of the file, pinned by two tests that fail if it moves again.
- The long-press gesture no longer fires on the key press that wakes the pad; the panel
  simulator no longer drops the key id it was asked to press; configuration history lists
  revisions by write time, so several edits in one second keep their order.

### Measured

| Metric | Before | 1.2.0 |
|---|---|---|
| Flash | 1 004 527 B (76 %) | 1 006 811 B (76 %) |
| Static RAM | 181 548 B (55 %) | 173 364 B (52 %) |
| MQTT callback frame | 23 424 B | 6 704 B |
| Arduino loop stack | 32 KiB | 16 KiB (≈ +16 KiB heap at runtime) |
| Diagnostics payload | 9 fields | 10 fields, worst case 254 B of a 320 B cap |

Internal-heap gain from the fix set: ≈ 24.7 KB. Flash is not the constraint (≈ 303 KB free).

### Verified

832 pytest plus the tests added with the release commit (44 browser), 199 firmware static, 193 release tests,
the host core binary, a real `arduino-cli` build for
`esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,PSRAM=opi`, plus ruff, mypy, the notice
audit and the public-release audit.

### Not verified on hardware

Nothing in this release has been flashed to a physical pad. What a flash and one press each
would close:

1. **The settings fix** - flash, configure once through the portal, restart: the pad must come
   up in normal mode with the panel showing the last page, and must *not* open the portal again.
2. **`stack_min`** - read `micropad/diag` after a full catalog parse: the number is the loop
   stack's remaining bytes (the linked frames predict 6,704 B used of 16 KiB).
3. **Long press and the four new item types** - hold Enter on a light (turns off), hold on a
   lock (unlocks), press a cover (toggles).
4. **Two behaviours worth watching** - the 250 ms sleep-predicate sampling, and the portal
   fallback after ~24 failed associations (it must not fire on a pad whose Wi-Fi is merely slow
   to come up).

### Deliberately not changed

Moving the catalog into PSRAM (≈ −122 KB static RAM) and shrinking `ENTITY_CAP` — both trade
a known-good failure mode for an unknown one and need their own change set with hardware
validation. No second protocol version, no cloud dependency, no scripting engine on the
device, no OTA outside the setup portal.

## 1.1.0 — clean-room baseline

Unreleased baseline for this branch; no changelog was kept. The firmware version it
publishes is `1.1.0` and the contract revision is `1`.
