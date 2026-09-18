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
  matching `bench-[0-9]{2}`, the exact 27 check IDs below all `true`, evidence
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

### `gesture-hold-binding`

- **Setup:** awake; a key bound to a `hold` action that differs from its tap
  (e.g. tap `toggle light.desk`, hold `off light.ceiling`), and one key that binds
  no hold at all.
- **Action:** hold each key for ≥1 s, then release.
- **Observable:** exactly one `micropad/event` with `gesture: "hold"` and the hold
  action/entity — **no** `tap` event, before or after. The key without a hold emits
  its ordinary tap (or the item's alternate action) on release and never a hold.
- **Evidence kind:** `log` (the event topic) + `timing-log`.
- **Pass gate:** one hold event per held key, no tap for a key that fired a hold,
  and the hold binding wins over the item's alternate action.
- **Why it is not host-only:** the host tests prove the state machine; only the real
  button bounce and the 600 ms feel can be judged here.

### `gesture-double-press`

- **Setup:** awake; one key bound to a `double` action (e.g. `press script.dim`) and
  one key with *no* double binding next to it.
- **Action:** (a) two presses inside 350 ms on the bound key; (b) two presses ~1 s
  apart on the same key; (c) two fast presses on the unbound key.
- **Observable:** (a) **one** event, `gesture: "double"` — no tap event; (b) two
  ordinary tap events, no double; (c) two ordinary tap events, with the *first* one
  arriving immediately (the unbound key must not be delayed by the gesture window).
- **Evidence kind:** `video` + `timing-log`.
- **Pass gate:** exactly one double event for (a), two taps for (b), and the
  unbound key's first tap latency indistinguishable from before this feature.

### `gesture-wake-press-exempt`

- **Setup:** pad asleep (light sleep), the wake key bound to a `hold` and a `double`
  gesture.
- **Action:** press and hold the wake key for ≥1 s; release; wait; then double-press
  it.
- **Observable:** the pad wakes and shows content, but **no** gesture event is
  published for the press that woke it. The next press behaves normally.
- **Evidence kind:** `video` + `log`.
- **Pass gate:** zero events for the waking press; aimed gestures afterwards work.

### `slider-adjust-encoder`

- **Setup:** awake; a page with one `control: slider` light row (0–100, step 5) and
  one ordinary row; Home Assistant running the generated automation.
- **Action:** select the slider row, turn the encoder (both directions and past both
  ends), then select the ordinary row and turn again.
- **Observable:** each detent publishes `adjust` with the **new** value and the real
  light (or `brightness_pct`) follows; at 0 %/100 % further detents in that direction
  do **nothing at all** — no event, no cursor move (the value is at its limit and the
  limit holds) — and the opposite direction still steps away from the bound; on the
  ordinary row the encoder scrolls as it always did. Pressing Enter on the slider row
  does nothing new. **Check the escape explicitly:** with the cursor parked on a
  slider at its bound, a key bound to `scroll_down` (or `back`/`home`) must still
  leave the row — that is the documented way off a page of sliders parked at the same
  end, and it is why the encoder's shipped default stays plain scrolling.
- **Evidence kind:** `video` + `log`.
- **Pass gate:** value tracks the detents in both directions, clamps at both ends
  without publishing an out-of-range value, a turn into the bound changes nothing,
  the escape key still scrolls, and the level in Home Assistant matches the panel
  within one refresh.
- **Also check:** e-paper **ghosting** at the track/fill boundary — every detent
  redraws the bar, which is the fastest partial-refresh rate the panel will see.

### `checkbox-value-column`

- **Setup:** a page with an `on`/`off` switch row, a `true`/`false` row, a `cover`
  reporting `open`, a `lock` reporting `locked`, a `sensor` row with a numeric state
  and a `media_player` row reporting `playing`.
- **Action:** look at the value column, then toggle the switch row (waiting for the
  refresh) and look again.
- **Observable:** every two-valued row shows a box — ticked for *on*/*true*/*open*/
  *locked*, empty for the opposite member of its pair — and the words `on`/`off` (or
  `open`/`closed`, `locked`/`unlocked`) appear nowhere on the panel; the sensor keeps
  its numeric text and the media row its state text (`playing` is not a tick).
- **Evidence kind:** `photo`.
- **Pass gate:** boxes are unambiguous at arm's length, the tick is legible at the
  panel's 4-px line weight, and no boolean row shows text.

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
- **Action:** power on and observe the setup access point, then exit and
  re-enter the setup portal (or reboot twice).
- **Observable:** AP `MicroPad-Setup` appears and the panel shows a
  **16-character password**. The password is **generated once from hardware
  entropy and persisted in NVS** — the same password is shown on every later
  portal entry, so a phone that already joined reconnects with the saved
  network instead of a new code.
- **Evidence kind:** `photo` + `manual-observation`.
- **Pass gate:** AP name present, a 16-character password shown, and the same
  password after portal exit/re-entry.

### `portal-never-sleeps`

- **Setup:** setup portal open, battery powered.
- **Action:** leave the portal idle past the normal battery idle-sleep
  threshold (~60 s), then keep waiting.
- **Observable:** the portal stays fully awake — the AP keeps beaconing, the
  DNS/web server keeps answering, the phone stays connected. No light-sleep
  entry while the portal is active; the portal's own
  ≈5-minute inactivity restart is the only shutdown path.
- **Evidence kind:** `video` + `manual-observation`.
- **Pass gate:** no sleep entry while the portal is open (sleep only after
  portal exit / settings saved).

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