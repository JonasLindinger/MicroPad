# MicroPad Firmware Guide

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

This guide covers building and flashing the MicroPad firmware
(`firmware/MicroPad_HA_Controller.ino`, `firmware/micropad_core.*`,
`firmware/protocol_contract.h`). It is written in English and contains no real
credentials — only the neutral placeholders described below.

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
  heap, where the 16-KiB MQTT buffer and the WiFi/lwIP buffers live.

## Not safety-critical

This firmware drives an e-paper controller and sends MQTT events; it is a
convenience input device, not a safety mechanism. It is provided as-is, without
warranty, and must be verified on your own hardware. Do not use it for
safety-critical applications.