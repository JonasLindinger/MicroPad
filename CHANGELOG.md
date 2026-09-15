# Changelog

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

Two numbers describe a build. The **firmware version** is what the pad publishes on
`micropad/device` and what the configurator shows; the **contract revision** is the wire
format both sides speak. The clean-room line was merged into `main` through PR #9 and is
released from there; the entries below are grouped by the release they shipped in.

## [Unreleased]

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
