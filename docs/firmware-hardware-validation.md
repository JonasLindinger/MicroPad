# MicroPad Firmware Hardware Acceptance Gate

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

This document is a repeatable, row-by-row hardware acceptance gate for the clean-room MicroPad firmware. Execution requires a physically attached MicroPad (ESP32-S3 + GxEPD2_290_T94_V2 e-paper + 3×4 matrix + rotary encoder + Li-Ion/BQ24075 + USB-CDC), a running MQTT broker on the documented test namespace, and a Home Assistant instance (or its MQTT bridge) to reconcile state. Every row records an **observed result** and a **pass/fail**; no row may be marked pass from compilation or reasoning alone.

**IMPORTANT — observation rows.** The Final Verification Gate scripted on this build machine (Step 8) has no MicroPad hardware, so *all* acceptance rows below are intentionally left **unchecked** in this committed revision. The compile matrix and static/host suites prove the firmware builds green and the source asserts hold; they prove *nothing* about the physical behaviour. Each hardware row must be executed by an operator on real hardware and marked from direct observation.

> **Hardware validation not executed: compatible MicroPad hardware unavailable**

---

## 1. Executed environment (record at run time)

| Field | Value |
|---:|---|
| Firmware commit SHA | `48b970ac347acbf76fad7f1cfe1f6c4bed190a63` |
| Compile-mode FQBN (matrix row used to flash) | `esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,PSRAM=opi` |
| Arduino CLI | 1.5.1 |
| ESP32 (esp32:esp32) core | 3.3.11 |
| GxEPD2 | 1.6.9 |
| ArduinoJson | 7.4.3 |
| PubSubClient | 2.8 |
| Broker test namespace | `micropad` (topics below) |
| Broker host (authoritative config via on-device portal) | `192.168.0.100` (placeholder; real credentials are never in source) |
| MQTT port | `1883` |
| Run start (UTC) | *fill in* |
| Run end (UTC) | *fill in* |
| Operator / notes | *fill in* |

> **Credentials.** Source contains only the neutral placeholders (`192.168.0.100`, user `micropad`, password `replace-me`). Real Wi-Fi/MQTT credentials are entered **only through the on-device captive portal** and stored in NVS; never edit firmware, `micropad_core.*`, scripts, or this document to embed real credentials.

**Flash command (record the exact binary and upload line).**

```bash
# Stage produces build/arduino/MicroPad_HA_Controller/ from the sourced firmware.
./scripts/compile-firmware.sh --one esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,PSRAM=opi
# Binary (build-generated path, confirm before upload):
#   build/arduino/MicroPad_HA_Controller/build/.../*.bin
# Upload, e.g.:
#   arduino-cli upload -p /dev/ttyACM0 --fqbn <FQBN> build/arduino/MicroPad_HA_Controller/
```

Record each emitted `micropad/event`, `micropad/power`, and received `micropad/pages/all`, `micropad/page/current`, `micropad/keymap` payload verbatim in the row's notes.

**Carried-in acceptance items.** The following were recorded as Important carries to T14 and are explicit acceptance items, not footnotes:

- **HA template round-trip (retained catalog/current-page).** The backend publishes retained `micropad/pages/all`, `micropad/page/current`, and `micropad/keymap` payloads. If those payloads carry HA template strings (`{{ states(...) }}`, etc.), the firmware parsers reject them because they accept only concrete values. During the MQTT cache row (Step 5b below), capture the **actual retained bytes** of `micropad/pages/all` and `micropad/page/current` on-device and confirm they contain concrete states, not templates. If the retained payloads carry literal `{{ ... }}` templates, the backend publish path must be fixed to publish concrete payloads (or the parsers relaxed) — do not ship templated retained payloads to the firmware.
- **`CONFIG_ARDUINO_LOOP_STACK_SIZE`.** The 16-KiB MQTT callback buffer plus a `Page` (and `Binding[KEY_COUNT]`) on the Arduino loop-task stack exceeds the default 8-KiB loop-task stack. The firmware setup/build must raise `CONFIG_ARDUINO_LOOP_STACK_SIZE` (**sdkconfig/setup bump**, e.g. via `-DCONFIG_ARDUINO_LOOP_STACK_SIZE=32768` in the compile build-properties, or a target-specific `sdkconfig`). Confirm the callback path does not overflow the loop task under a full `micropad/pages/all` payload. *(The compile matrix in Task 13 compiles with the pinned properties; add and verify the stack bump as part of hardware bring-up.)*
- **Loop-task TWDT through light sleep.** A sleep longer than the default loop-task watchdog period can trip a post-sleep watchdog reset. The firmware removes/suspends the render task and the mitigation is `esp_task_wdt_deinit()` at sleep entry with re-init on wake. Row W1 (Step 7) must validate a >60 s sleep explicitly and confirm no watchdog reset after wake.
- **`renderBusy` race window.** Core 0 can claim a render notify between the sleep gate's acquire-load and `vTaskSuspend`, freezing mid-`drawSnapshot`; it resumes post-wake and completes. Row W3 (Step 7) must confirm no ghost pixels appear after resume.

---

## 2. Result notation

- `[ ]` **unchecked** — not executed (no hardware on this build machine), or not yet observed.
- `[x]` **PASS** — observed directly and matching the numbered acceptance criterion.
- `[x]` **FAIL** — observed directly and not matching; record the observed bytes/times in the notes.

---

## Step 4. Matrix and encoder acceptance

### 4.1 Physical inputs (one MQTT event per debounced press, no repeat while held)

For each input, press once and observe exactly one `micropad/event` publish (after the 25 ms debounce), then hold and observe **no further** publish while still held (release emits one `Released` edge only when an action uses the release edge).

| Input | Observed result | Pass/Fail | Emitted `micropad/event` payload |
|---:|---|---:|---|
| `r0c0` | *record* | [ ] | *record* |
| `r0c1` | *record* | [ ] | *record* |
| `r0c2` | *record* | [ ] | *record* |
| `r0c3` (encoder push / enter) | *record* | [ ] | *record* |
| `r1c0` | *record* | [ ] | *record* |
| `r1c1` | *record* | [ ] | *record* |
| `r1c2` | *record* | [ ] | *record* |
| `r1c3` | *record* | [ ] | *record* |
| `r2c0` | *record* | [ ] | *record* |
| `r2c1` | *record* | [ ] | *record* |
| `r2c2` | *record* | [ ] | *record* |
| `r2c3` | *record* | [ ] | *record* |
| `enc_up` | *record* | [ ] | *record* |
| `enc_down` | *record* | [ ] | *record* |

### 4.2 Encoder direction and selection increment

| Criterion | Observed result | Pass/Fail |
|---:|---|---:|
| Clockwise/right detent emits `enc_down` and selection moves `+1` | *record* | [ ] |
| Counterclockwise/left detent emits `enc_up` and selection moves `-1` | *record* | [ ] |
| One encoder step per detent, no repeat while the knob stays in a detent | *record* | [ ] |

### 4.3 Rebinding changes behaviour without reflashing

Prove that a per-page `micropad/keymap` update changes behaviour on the device without a reflash: publish a keymap rebinding `r0c0` (and `enc_up`/`enc_down`) to different actions, observe the new action on the next press/detent, then restore. Record the before/after payloads.

| Criterion | Observed result | Pass/Fail |
|---:|---|---:|
| Rebinding a matrix key changes its action without reflashing | *record* | [ ] |
| Rebinding `enc_up`/`enc_down` changes selection/action without reflashing | *record* | [ ] |

### 4.4 Held key during refresh

During a display refresh (busy), hold a key and confirm the input state change is applied before the refresh completes (input is scanned on Core 1 continuously; the refresh never starves matrix/encoder input).

| Criterion | Observed result | Pass/Fail |
|---:|---|---:|
| Key held during an active refresh changes input state before refresh completion | *record* | [ ] |

---

## Step 5a. Captive-portal acceptance

| # | Criterion | Observed result | Pass/Fail |
|--:|---:|---|---:|
| P1 | First boot with no saved settings shows AP `MicroPad-Setup` with a 16-character password on the panel; the password persists in NVS and the same password appears on portal re-entry | *record* | [ ] |
| P2 | Matrix/encoder remain responsive while the portal is open | *record* | [ ] |
| P3 | Back/cancel exits the portal leaving NVS/settings unchanged (no save, no reconnect to saved network) | *record* | [ ] |
| P4 | Valid save stores Wi-Fi/MQTT credentials, restarts the AP, and reconnects to the station network + broker | *record* | [ ] |
| P5 | Portal restarts (inactivity) near 300 000 ms (≈5 min) of no activity | *record elapsed ms* | [ ] |

---

## Step 5b. MQTT cache and queue acceptance

| # | Criterion | Observed result | Pass/Fail |
|--:|---:|---|---:|
| Q1 | Disconnect the broker, perform discrete actions (navigate, toggle, press) and value edits; reconnect | *record* | [ ] |
| Q2 | After reconnect, the queue flushes **at most four** events per `loop()` pass (via the compile-time debug counter — `#if MICROPAD_DEBUG`), **without CDC output** | *record counter* | [ ] |
| Q3 | Retained `micropad/pages/all` catalog, `micropad/page/current`, and `micropad/keymap` are recovered on (re)connect | *record* | [ ] |
| Q4 | **Carried-in:** capture the actual retained `micropad/pages/all` / `micropad/page/current` bytes and confirm concrete values, not `{{ states(...) }}` templates (see §1) | *record bytes* | [ ] |

---

## Step 5c. Predictive display and resync acceptance

| # | Criterion | Observed result | Pass/Fail |
|--:|---:|---|---:|
| R1 | Immediate local navigation/toggle/number rendering appears without waiting for the broker | *record* | [ ] |
| R2 | Authoritative correction arrives after the 350 ms quiet-period resync when local and authoritative disagree | *record* | [ ] |

---

## Step 6. Display and USB/power acceptance

### 6.1 Refresh policy

| # | Criterion | Observed result | Pass/Fail |
|--:|---:|---|---:|
| D1 | Boot performs a full refresh | *record* | [ ] |
| D2 | Ten partial refreshes followed by an 11th forced full refresh | *record counts* | [ ] |
| D3 | Portal enter and portal exit each perform a full refresh | *record* | [ ] |
| D4 | UI is readable: title/status strip, four item rows, selection cursor, conditional scrollbar, and network-status cell | *record* | [ ] |
| D5 | **No per-row waveform / ghost banding** — every partial refresh is a single full-native-panel `setPartialWindow(0,0,128,296)` window | *record* | [ ] |

### 6.2 USB-CDC host connect/disconnect (enumerated data host)

| # | Criterion | Observed result | Pass/Fail |
|--:|---:|---|---:|
| U1 | Connect an enumerated CDC host: **no** power icon before 1500 ms of stability | *record ms* | [ ] |
| U2 | After the 1500 ms stable window: power icon drawn **and** retained `micropad/power` `{"usb_host":true}` published | *record* | [ ] |
| U3 | While a host is enumerated, light sleep is prevented (no sleep entry) | *record* | [ ] |
| U4 | Host removal is debounced the same 1500 ms: icon removed and `{"usb_host":false}` published symmetrically | *record ms* | [ ] |
| U5 | A charge-only supply (no enumerated host) intentionally follows normal battery sleep behaviour (no power icon, light sleep allowed) | *record* | [ ] |

---

## Step 7. Warm-sleep and reconnect acceptance (battery / no host)

| # | Criterion | Observed result | Pass/Fail |
|--:|---:|---|---:|
| W1 | On battery/no host, light sleep after ≈60 000 ms idle; **carried-in:** validate a >60 s sleep with no post-sleep watchdog reset (TWDT mitigation) | *record ms* | [ ] |
| W2 | No sleep with a held key or an active/pending refresh (`renderBusy` / pending snapshot) | *record* | [ ] |
| W3 | Immediate matrix wake: the waking press becomes the first action; **carried-in:** confirm no ghost pixels from the renderBusy race window | *record* | [ ] |
| W4 | Encoder wake does not lose the next detent after wake | *record* | [ ] |
| W5 | No second sleep for at least 3000 ms after wake (post-wake brake) | *record ms* | [ ] |
| W6 | Wi-Fi association is preserved across sleep/wake (no reassociation) | *record* | [ ] |
| W7 | When sleep exceeded the 15 s MQTT keepalive, the stale socket is invalidated and MQTT reconnects immediately | *record* | [ ] |

---

## 3. Compile / static evidence (this build machine)

Executed and green on this build machine (no hardware):

| Check | Result |
|---:|---:|
| Host C++ behaviour suite (`scripts/test-firmware-host.sh` step 1) | PASS (exit 0, `-Wall -Wextra -Werror`) |
| Static contract suite (`tests/firmware/test_firmware_static.py`) | ALL PASS |
| Compile-matrix wrapper (`tests/firmware/test_compile_matrix.py`) | ALL PASS |
| Compile matrix: HWCDC/JTAG + PSRAM (`USBMode=hwcdc,CDCOnBoot=cdc,PSRAM=opi`) | exit 0 |
| Compile matrix: TinyUSB CDC + PSRAM (`USBMode=default,CDCOnBoot=cdc,PSRAM=opi`) | exit 0 |
| Compile matrix: CDC-disabled / no PSRAM (`USBMode=hwcdc,CDCOnBoot=default,PSRAM=disabled`) | exit 0 |
| `git diff --check` | silent |
| Working tree after the acceptance commit | clean (only the doc + static-test change in the commit) |

The compile and static results do **not** transmit to any hardware row; hardware rows stay unchecked until executed.

---

## 4. Release assertions encoded in `tests/firmware/test_firmware_static.py`

The final static layer pins, in source:

- **Step 1a** — exact pins (CS8/DC40/RST41/BUSY42, rows 47/48/45, cols 21/14/13/12, encoder A10/B11), active-low row drive + restore, and all fourteen bindable input IDs (`r0c0`..`r2c3`, `enc_up`, `enc_down`) with `KEY_COUNT == 14`.
- **Step 1b** — 16384-byte MQTT buffer, 16-slot queue, 4-event flush cap, exactly three subscriptions (`micropad/pages/all`, `micropad/page/current`, `micropad/keymap`), single-pass deserialization, and one first-connect `get_all_pages`.
- **Step 1c** — two fixed render slots (`SLOT_COUNT == 2`), the two independent sleep power guards, 500/1500 ms USB sample/stable window, only the full-native-panel partial window, `display.init(0)`, `DISABLE_DIAGNOSTIC_OUTPUT`, and no unconditional draw in `loop()`.
- **Step 1d** — neutral placeholder credentials only (no real/routable IP, no `mqtt://`/`wss://`), no CDC/Serial writes on any hot path, and the exact AI-assisted notice in **every** firmware, test, script, workflow, config, `.gitignore`, and firmware-document file.