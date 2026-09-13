# MicroPad Hardware Acceptance

> AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.

This document is a repeatable, row-by-row hardware acceptance gate for a physical
MicroPad bench unit. It is the English release companion to
`docs/firmware-hardware-validation.md`, which holds the full per-row checklists and
the compile/static evidence. Every check below records an **observed result** and a
**pass/fail**; no check may be marked pass from compilation or reasoning alone.

**IMPORTANT — observation rows.** No MicroPad hardware was available on the build
machine during development, so all physical device checks are intentionally left
**unchecked** in the committed revision, exactly as the F14/IT precedent did. The
compile matrix and the host/static suites prove the firmware builds green and the
source asserts hold; they prove *nothing* about physical behaviour. Each check must
be executed by an operator on real hardware and marked from direct observation.

> **Hardware validation not executed: compatible MicroPad hardware unavailable**

Credentials are the neutral placeholders only: broker host `192.168.0.100`, user
`micropad`, password `replace-me`. Real credentials are entered only through the
on-device setup portal and stored in NVS; never edit firmware, source, or docs to
embed real credentials.

## Record file, schema, and validator

The operator records every check in an anonymous, local acceptance record:

- **File (never committed):** `release/local-hardware-acceptance.json`
- **Strict contract:** `release/hardware-acceptance.schema.json`
  (`schema_version: 1`, 64-lowercase-hex `firmware_sha256`, anonymous `board_id`
  matching `bench-[0-9]{2}`, the exact 21 check IDs below all `true`, evidence
  entries `{check_id, kind, sha256, captured_at}`, `additionalProperties: false`
  throughout).
- **Validator:** `scripts/validate_hardware_acceptance.py` validates the record and,
  only when it is fully valid, writes the redacted
  `build/evidence/hardware-acceptance-summary.json` (check IDs, booleans,
  timestamps, firmware SHA-256, per-check evidence SHA-256). No operator, Wi-Fi,
  MQTT, Home Assistant, broker host, or device-entity data ever enters the summary.

```bash
.venv/bin/python scripts/validate_hardware_acceptance.py \
    --input release/local-hardware-acceptance.json \
    --output build/evidence/hardware-acceptance-summary.json
```

An incomplete record exits nonzero and lists every missing/invalid check; a fully
passing bench exits `0`. The local record stays ignored; only the redacted summary
joins the approval bundle.

## Equipment

- **Board:** ESP32-S3 (USB-CDC), wired as in the pin map in `docs/firmware.md`.
- **Display:** GxEPD2_290_T94_V2 2.9" e-paper (native 128x296, driven at rotation 1 as a 296x128 landscape canvas), CS8/DC40/RST41/BUSY42.
- **Inputs:** 3×4 matrix rows 47/48/45, columns 21/14/13/12; rotary encoder A10/B11
  (push = `r0c3` enter).
- **Power:** Li-Ion through a BQ24075 charge path; USB-C host or charge-only supply.
- **Network:** an MQTT broker on the host placeholder `192.168.0.100:1883` and a
  Home Assistant instance (or MQTT bridge) to reconcile state.

Timing checks use a hand stopwatch or the on-device/timing log; reference times are
exact thresholds, not approximations.

## Power and sleep

### `usb-host-icon-after-1500ms` — data-host icon edge-in

- **Setup:** bench unit awake on battery; attach an enumerated **USB-C data host**.
- **Action:** connect the data host and leave the device idle.
- **Observable:** **no** power icon before 1500 ms of host stability; after the
  stable window the power icon is drawn and the retained `micropad/power`
  `{"usb_host":true}` is published. Icon-edge interval measured **1.3–1.8 s**.
- **Evidence kind:** `video` (icon edge) + `timing-log` (edge interval).
- **Pass gate:** edge within 1.3–1.8 s and `usb_host:true` observed once.

### `usb-disconnect-clears-after-1500ms` — data-host icon edge-out

- **Setup:** data host enumerated (prior check state).
- **Action:** detach the data host.
- **Observable:** host removal is debounced the same window and the icon is cleared
  with `{"usb_host":false}` published; edge interval measured **1.3–1.8 s**.
- **Evidence kind:** `video` (icon removal) + `timing-log` (edge interval).
- **Pass gate:** `usb_host:false` observed once, edge within 1.3–1.8 s.

### `usb-host-prevents-sleep`

- **Setup:** enumerated data host connected, device awake.
- **Action:** leave idle past the normal idle-sleep threshold.
- **Observable:** while a data host is enumerated the device **never** enters light
  sleep — no sleep entry, no power icon change.
- **Evidence kind:** `timing-log` (elapsed idle) + `manual-observation`.
- **Pass gate:** no sleep entry for ≥ the idle-sleep threshold while host is up.

### `charge-only-follows-battery-sleep`

- **Setup:** attach a **charge-only wall charger** (no enumerated host).
- **Action:** leave idle past the normal idle-sleep threshold.
- **Observable:** the charger does **not** inhibit sleep — no power icon, and light
  sleep is entered at the normal battery idle threshold (mirror of the host case).
- **Evidence kind:** `timing-log` + `video`.
- **Pass gate:** light sleep entered at normal idle threshold; no power icon.

### `battery-idle-sleeps-near-60s`

- **Setup:** on battery, no USB host, freshly woken, no held keys.
- **Action:** stop touching the device; measure with a stopwatch.
- **Observable:** light sleep after ≈60 000 ms of idle; stopwatch threshold
  **55–70 s**; **no post-sleep watchdog reset** (loop-task TWDT mitigation).
- **Evidence kind:** `timing-log` (stopwatch) + `serial-disabled` (no marker output).
- **Pass gate:** sleep between 55 and 70 s; no watchdog reset after wake.

### `wake-action-immediate`

- **Setup:** device asleep on battery (prior sleep check).
- **Action:** wake with a matrix press or an encoder detent.
- **Observable:** the **waking press becomes the first action** and its effect is
  visible in the **first rendered post-wake snapshot**; no ghost pixels from the
  renderBusy race window; the encoder wake does not lose the next detent.
- **Evidence kind:** `video` (first post-wake snapshot) + `manual-observation`.
- **Pass gate:** first post-wake snapshot shows the waking action; no ghosting.

## Input

Input IDs: matrix `r0c0`…`r2c3` (12 IDs) plus `enc_up` and `enc_down` (14 bindable
IDs, `KEY_COUNT == 14`). Every press must emit exactly one `micropad/event` after
the 25 ms debounce, with no repeat while held.

### `matrix-all-twelve`

- **Setup:** awake, default keymap.
- **Action:** press each of the twelve matrix keys once.
- **Observable:** exactly one `micropad/event` per debounced press, no repeat while
  held, and all **twelve** matrix IDs (`r0c0`…`r2c3`) emit their expected binding.
- **Evidence kind:** `video` (visible presses) + `manual-observation`.
- **Pass gate:** all 12 IDs emit exactly once per press; no repeats while held.

### `encoder-clockwise-down`

- **Setup:** awake, on a page with a selection.
- **Action:** one clockwise/right detent.
- **Observable:** emits `enc_down` and the selection moves **+1**; one step per
  detent with no repeat while the knob stays in a detent.
- **Evidence kind:** `video` + `timing-log`.
- **Pass gate:** `enc_down` on clockwise detent and selection +1.

### `encoder-counterclockwise-up`

- **Setup:** awake, on a page with a selection.
- **Action:** one counterclockwise/left detent.
- **Observable:** emits `enc_up` and the selection moves **−1**; one step per detent.
- **Evidence kind:** `video` + `timing-log`.
- **Pass gate:** `enc_up` on counterclockwise detent and selection −1.

### `encoder-push-r0c3`

- **Setup:** awake, on a page where `r0c3` is the enter action.
- **Action:** press the enclosure **push** button on the encoder.
- **Observable:** the push acts as `r0c3` (enter): one `micropad/event` for `r0c3`
  and the mapped enter action fires.
- **Evidence kind:** `video` + `manual-observation`.
- **Pass gate:** push produces a single `r0c3` event and the enter action.

### `input-during-refresh`

- **Setup:** awake, a display refresh is about to be triggered.
- **Action:** trigger a refresh and hold a key during the BUSY window.
- **Observable:** the input state change is applied **before the refresh
  completes** — input scanning on Core 1 never starves matrix/encoder.
- **Evidence kind:** `video` + `manual-observation`.
- **Pass gate:** the held key registers before the refresh finishes.

## Display

The panel is a 2.9-inch e-paper (native 128x296) driven at rotation 1 as a 296x128
landscape logical canvas. "Four visible rows" refers to the readable item list
that must render fully across the 296-px-wide landscape canvas.

### `boot-full-refresh`

- **Setup:** device powered down/off.
- **Action:** power on / reset.
- **Observable:** boot performs a **full refresh** — the whole 128×296 panel clears
  and redraws before any partial content appears.
- **Evidence kind:** `video`.
- **Pass gate:** full-panel refresh visible once at boot.

### `display-four-rows`

- **Setup:** a page with at least four bindings, default keymap.
- **Action:** navigate to that page.
- **Observable:** the UI is readable: title/status strip, **four item rows**,
  selection cursor, conditional scrollbar, and network-status cell all render
  clearly in the 296×128 landscape canvas.
- **Evidence kind:** `photo`.
- **Pass gate:** four item rows fully visible and readable.

### `full-panel-partial-refresh`

- **Setup:** awake on a page with multiple rows.
- **Action:** trigger a navigation partial refresh.
- **Observable:** every partial refresh is a **single full-native-panel**
  `setPartialWindow(0,0,128,296)` window — no per-row windows and no partial-row
  striping.
- **Evidence kind:** `video` + `timing-log`.
- **Pass gate:** partial refresh uses the full native 128×296 window each time.

### `ghosting-bound`

- **Setup:** awake; perform several refreshes.
- **Action:** run repeated (partial) refreshes over the same area.
- **Observable:** **no per-row waveform / ghost banding** — prior content leaves no
  residual banding layer across refreshes (bounded ghosting).
- **Evidence kind:** `photo` + `manual-observation`.
- **Pass gate:** no visible residual banding after repeated refreshes.

## Connectivity

### `portal-random-password`

- **Setup:** first boot with no saved settings.
- **Action:** power on and observe the setup access point.
- **Observable:** AP `MicroPad-Setup` appears and the panel shows a
  **16-character random password** (generated, never stored in firmware/source/docs).
- **Evidence kind:** `photo` + `manual-observation`.
- **Pass gate:** AP name and a 16-character random password shown on the panel.

### `portal-input-responsive`

- **Setup:** setup portal open.
- **Action:** operate matrix/encoder while the portal is open.
- **Observable:** matrix and encoder remain **responsive** while the portal is open —
  inputs are never starved by the portal loop.
- **Evidence kind:** `video` + `manual-observation`.
- **Pass gate:** inputs respond while the portal is open.

### `portal-back-no-save`

- **Setup:** portal open with unsaved edits.
- **Action:** select **Back** / cancel.
- **Observable:** the portal exits leaving **NVS/settings unchanged** — no save and
  no reconnect to any saved network.
- **Evidence kind:** `video` + `manual-observation`.
- **Pass gate:** after Back, no stored credential change and no reconnect.

### `wifi-mqtt-nonblocking`

- **Setup:** device connected to station network and broker.
- **Action:** navigate and toggle while the network transmits/reconnects.
- **Observable:** Wi-Fi/MQTT operations **never block** matrix/encoder input (Core 1
  input scanning continuous), and there is **no USB CDC output on any hot path**.
- **Evidence kind:** `video` + `serial-disabled`.
- **Pass gate:** responsive input throughout; no CDC output on hot paths.

### `mqtt-reconnect-resubscribe`

- **Setup:** device connected; broker running.
- **Action:** interrupt the broker link (stop then restore the broker), then wait.
- **Observable:** the stale socket is invalidated and MQTT **reconnects immediately**
  after the interruption, **resubscribing** to `micropad/pages/all`,
  `micropad/page/current`, and `micropad/keymap`; retained payloads are recovered on
  (re)connect with concrete values.
- **Evidence kind:** `video` + `timing-log`.
- **Pass gate:** reconnect immediate; all three retained topics recovered.

### `authoritative-resync`

- **Setup:** local display diverges from authoritative state.
- **Action:** publish an authoritative update, then leave the device idle.
- **Observable:** an **authoritative correction arrives after the 350 ms quiet-period
  resync** when local and authoritative disagree; retained `micropad/pages/all` and
  `micropad/page/current` carry concrete states, not `{{ states(...) }}` templates.
- **Evidence kind:** `video` + `manual-observation`.
- **Pass gate:** correction arrives after resync; no templated retained payloads.

MicroPad is not safety-critical. It is provided as-is, without warranty, and every
row here must be verified on your own hardware before relying on the device.