# MicroPad Firmware Guide

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

This guide covers building and flashing the MicroPad firmware
(`firmware/MicroPad_HA_Controller.ino`, `firmware/micropad_core.*`,
`firmware/protocol_contract.h`). It is written in English and contains no real
credentials — only the neutral placeholders described below.

## Contract

`contracts/mqtt-contract.json` is the single source of truth for the wire
protocol: version, topics, key IDs, actions, item types, the descriptor row per
item type, the payload ceilings and the device caps. Everything else is derived
from it:

- `firmware/protocol_contract.h` is **generated** —
  `python3 scripts/generate_contract.py --write` after a contract change, and
  `--check` is the drift gate (also asserted by
  `tests/integration/test_mqtt_contract.py`). Never hand-edit the header.
- `src/micropad/constants.py` and `src/micropad/ui_meta.py` read the contract at
  import time, so the backend, `/api/meta` and the browser cannot lag behind it.
- the browser's `static/js/contracts.js` key-ID/version block is generated too.

Adding an action, an item type, a topic or a limit is therefore a contract edit
plus a regenerate — no code path has to be kept in sync by hand. A static test
additionally asserts that the firmware's `Action`/`ItemType`/`KeyId` enums are in
contract order, that `micropad_core.h`'s caps equal the contract's, and that
`micropad_core.cpp`'s `ITEM_TYPE_DESCRIPTORS` table matches `item_type_meta`.

## Pin map

| Function | GPIO(s) |
|---|---|
| E-Paper CS / DC / RST / BUSY | 8 / 40 / 41 / 42 |
| Matrix rows (active-low) | 47, 48, 45 |
| Matrix columns | 21, 14, 13, 12 |
| Encoder A / B | 10 / 11 |

The display is a GxEPD2_290_T94_V2 2.9" e-panel (`128×296`, full-width partial
windows). Three rows × four columns plus the two encoder detents give the
fourteen bindable input IDs: `r0c0 … r2c3`, `enc_up`, `enc_down`. Rows are
driven active-low and restored after scanning.

## Build

The toolchain is pinned to Arduino CLI **1.5.1**, ESP32 core **3.3.11**,
GxEPD2 **1.6.9**, ArduinoJson **7.4.3**, and PubSubClient **2.8**. Preface the
commands with the repo `python3`-based workflows as needed; the toolchain script
is self-contained bash.

```bash
./scripts/install-arduino-toolchain.sh          # install + verify the pins (rootless, repo-local .arduino/)
./scripts/compile-firmware.sh --print-matrix    # show the three pinned FQBNs
./scripts/compile-firmware.sh                   # stage + compile all matrix FQBNs
./scripts/compile-firmware.sh --one <FQBN>      # stage + compile a single validated FQBN
./scripts/compile-firmware.sh --stage-only <FQBN>  # stage only (no toolchain), into build/arduino/
```

The script stages the reviewed sources into
`build/arduino/MicroPad_HA_Controller/`, compiles with `--warnings all`
plus `-Werror=return-type`, and compiles the three FQBNs:

```
esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,PSRAM=opi
esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=cdc,PSRAM=opi
esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=default,PSRAM=disabled
```

## Flash

The recommended upload FQBN for a USB-CDC data host is
`esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,PSRAM=opi`. Upload a staged
sketch with Arduino CLI against its enumerated serial device:

```bash
arduino-cli upload \
  --config-file arduino-cli.yaml \
  -p /dev/ttyACM0 \
  --fqbn "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,PSRAM=opi" \
  build/arduino/MicroPad_HA_Controller/
```

Do not embed real Wi-Fi or MQTT credentials in the firmware; they are entered
at runtime through the setup portal and stored in NVS.

## Setup portal

On first boot (or when no network settings are saved) the device starts a captive
portal AP named `MicroPad-Setup` with a 16-character password shown on the
panel. The password is generated once from hardware entropy, stored in NVS, and
**reused by every later portal entry** — it never rotates between sessions, so a
phone that already joined the AP reconnects with the same saved network. From
the portal you save the station Wi-Fi credentials and the MQTT broker
settings (host, port, user, password), then the device restarts its AP and
reconnects to the network and broker. The matrix and encoder remain responsive
while the portal is open; `Back` cancels and leaves settings unchanged; the
portal restarts itself after roughly 5 minutes of inactivity. Entering the portal
is bound to the `settings` action. While the setup portal is open the device
**never enters light sleep** — sleeping would silence the AP, DNS and web server
mid-setup.

The portal is also reachable without a working keymap: if the pad has stored
settings but cannot associate for 24 consecutive attempts (~2 minutes), it
re-opens the setup portal by itself, so a mistyped Wi-Fi password or a moved
access point cannot strand the device. The panel shows the portal view, and
`Back` (bound by default) cancels it and keeps the stored settings.

A save is applied atomically in the sense that the NVS commit marker (`cfg_ver`)
is cleared before the first field is written and set to `1` only after every
field was stored. An interrupted save is therefore an invalid record, and the
next boot falls back to the setup portal instead of connecting with a mixture of
old and new credentials.

The broker host placeholder used in source is `192.168.0.100`, the user
placeholder is `micropad`, and the password placeholder is `replace-me`. Replace
these only through the portal.

## Sleep

When the device is on battery and idle for about 60 seconds **and not powered by
a USB data host**, it enters warm light sleep (`esp_light_sleep_start`), waking on
the matrix or encoder (EXT1, ANY_LOW). Behaviours worth knowing:

- Sleep is refused while USB-powered (an enumerated CDC data host keeps the device
  awake and draws the power icon).
- A **charge-only wall charger** (no enumerated host) intentionally follows normal
  battery sleep behaviour: no power icon and light sleep allowed.
- The 15-second MQTT keepalive is used to invalidate a stale socket after a long
  sleep, so MQTT reconnects immediately on wake.
- The render task's watchdog entry is removed before sleep and restored after
  wake; matrix rows are driven low and restored on wake so the waking press
  becomes the first action.
- After wake there is a short brake so the device does not immediately re-sleep.

For the full acceptance rows (including the >60 s sleep, watchdog, and ghost-pixel
checks) see `docs/hardware-acceptance.md`.

## Input, payload and stack limits

- **Values are never rejected for being long.** Item names, page titles, states
  and units are *clipped* to their 32-character fields (the renderer shows 12
  characters of a value and 22 of a title), so a long Home Assistant state — for
  example a template sensor with a verbose string — cannot discard a whole page
  or catalog payload. Identifiers (`page_id`, `entity`, `target_page`) keep the
  strict check: a silently clipped entity id would address the wrong device.
- **The MQTT buffer bounds the catalog.** The client buffer is 16 KiB
  (`MQTT_BUFFER_BYTES`, mirrored in `contracts/mqtt-contract.json`); a retained
  `micropad/pages/all` that exceeds it is discarded by PubSubClient without
  telling the application. While the pad is connected but has no catalog, it
  re-requests the catalog every 60 seconds, so a lost publication is repaired
  instead of leaving an empty page.
- **The loop task stack is 16 KiB.** The MQTT callback parses the PubSubClient
  receive buffer in place (no payload copy), which keeps its frame near 7 KB;
  16 KiB leaves a ~2.3x margin and returns the rest of the former 32 KiB to the
  heap, where the 16-KiB MQTT buffer and the WiFi/lwIP buffers live. The value is
  the core constant `LOOP_TASK_STACK_BYTES`, so the sketch, the device-info
  payload and the tests cannot disagree about it.
- **The pad advertises its own limits.** On every MQTT connection the device
  publishes the *retained* payload `micropad/device`:

  ```json
  {"fw": "1.2.0", "contract": 2, "caps": {"pages": 24, "items": 20, "name": 32,
   "title": 32, "page_id": 32, "entity": 96, "state": 32, "unit": 16,
   "mqtt_buffer": 16384, "stack": 16384}}
  ```

  Every value is read from the firmware's own constants, so it describes what
  this build really enforces instead of what the backend assumes. A late
  subscriber (the backend, the configurator) gets it without asking, which is
  what lets `/api/meta` report the pad's real field caps and warn before a value
  would be clipped on the panel.

## Input gestures

Each of the fourteen inputs behaves the same whether it is tapped or held, with one
exception that costs no extra binding storage: **holding the Enter key fires the
item's *alternate* action instead of its default one.** The item-type descriptor
decides which types have one — a light or a switch turns **off** on a hold instead
of toggling; every other type ignores the hold (`alternate_action` in
`contracts/mqtt-contract.json`, mirrored by `ITEM_TYPE_DESCRIPTORS`). The gesture
dispatches an ordinary action, so the MQTT event, the Home Assistant automation and
the payload schemas are unchanged.

* `LONG_PRESS_MS` (600 ms, core) is measured from the *debounced* press, so a tap
  can never fire it, and it fires exactly once per hold.
* A hold keeps the pad awake (it counts as input activity) and therefore cannot
  race the 60 s sleep gate.
* The press that woke the device from light sleep is consumed by the immediate wake
  scan, so holding it does not also fire the gesture — otherwise holding the key
  that woke the pad would turn something off.

## Stored settings and the setup portal

Settings live in NVS under the namespace `micropad`, guarded by a `cfg_ver` commit marker that
is invalidated first and written last, so an interrupted save is an invalid record rather than a
mixture of old and new credentials. A record is accepted only when the marker is current, every
expected key exists, every value reads back inside its field, and the whole candidate validates
(`micropad::validateSettings`), which is defined in terms of `settingsError()` so the rule and
the message cannot drift apart. A rejected record means the pad starts its own setup portal -
which is also what makes the read path worth pinning:

* **Strings are read with string APIs** (`isKey()` for presence, `getString()` for the value,
  which refuses an oversized value instead of truncating) and integers with the type-aware
  `getType()`/`getUShort()` pair. `Preferences::getBytesLength()` must never be used for either:
  it is a *blob* accessor (it calls `nvs_get_blob`), and an IDF blob read on an entry written
  with `nvs_set_str` fails with `ESP_ERR_NVS_TYPE_MISMATCH`. It therefore reports 0 for a value
  that is present, which makes every stored string look absent, rejects a complete record, and
  sends a fully configured pad back into the portal on **every boot**. A static test pins the
  type-correct API usage precisely because this failure is silent and looks like a Wi-Fi problem.
* A refused save answers with the reason (`settingsError()`: which field and what is expected,
  never its value), so the portal is fixable in place instead of showing "invalid settings".

## Diagnostics

The payload carries `uptime_s`, `mqtt_connects`, the accept/reject counters per payload type,
`event_drops`, the heap free and heap high-water figures, and `stack_min` - the loop task's stack
high-water mark in bytes free, sampled with `uxTaskGetStackHighWaterMark()` from the loop task
itself. `stack_min` is what confirms the 16 KiB loop stack on real hardware: the linked call
frames say the worst callback uses 6,704 B, and this number shows how much was actually left.
The worst-case payload (ten 10-digit values) measures 254 bytes against a 320-byte cap.

Every connection publishes the retained `micropad/diag` payload, refreshed once a
minute while connected:

```json
{"uptime_s":3661,"mqtt_connects":3,"catalog_parses":2,"catalog_rejects":1,
 "page_rejects":4,"keymap_rejects":5,"event_drops":6,"heap_free":123456,
 "heap_min":100000}
```

The counters answer the questions a serial console used to answer: is the pad
reconnecting in a loop, did a retained payload get rejected, did events get dropped
because the queue was full, and how close did the heap get to exhaustion. The
struct and the formatter live in `micropad_core.h`/`.cpp` (`formatDiagnostics`,
host-tested byte for byte), so the sketch only bumps counters and publishes; the
payload is rendered into a 256-byte stack buffer with no JSON document, and the
formatter refuses to write rather than emitting a truncated object.

## Host simulator (panel preview)

`tools/micropad_sim.cpp` compiles `micropad_core.cpp` into a host binary that
answers "what does the panel render for this page, and what does this key do?":

```bash
./scripts/build-simulator.sh                 # g++ only, no Arduino toolchain
printf 'page home Home\nitem Desk light\t on\t\t0\tlight\tlight.desk\t\nkey r0c3\tenter\tlight.desk\t\npress r0c3\n' \
  | ./build/host/micropad_sim
```

It prints one JSON object: the row window, the prim list from `layoutNormalUi` /
`layoutPortalUi`, `would_reject` plus `findings` for anything the pad would clip or
refuse, and — when a `press` directive is given — what the binding and the item
under the cursor resolve to. The configurator's preview and budget panel are
driven by it (`docs/configurator.md`), and `tests/test_simulator.py` pins the
output, including a check that the recorded browser fixture still matches the
current core.

The tool contains no geometry or policy of its own: it reads a small directive
format and calls the core. `fillPageSnapshot()` in the core is the shared
snapshot builder used by both the sketch and the simulator, so the row window,
title and selection flags cannot differ between the panel and the preview.

## Not safety-critical

This firmware drives an e-paper controller and sends MQTT events; it is a
convenience input device, not a safety mechanism. It is provided as-is, without
warranty, and must be verified on your own hardware. Do not use it for
safety-critical applications.