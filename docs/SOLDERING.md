# MicroPad — Soldering Guide (SMD reflow)

> ⚠️ **AI-assisted documentation** — reviewed by the author, provided as-is, without
> warranty. Follow at your own risk.

This board is mostly **SMD** (0805 passives, a VQFN-16 charger, an SOT-23 LDO/ESD and the
ESP32 module). Hand soldering works for most parts; the **BQ24075 (VQFN-16, bottom pad)**
and the **ESP32-S3 module** are much easier with a **hot plate** (recommended) or hot air.

> **Short version:** paste → place small parts → reflow at ~250 °C plate → add switches &
> encoder (through-hole-ish) → clean → test.

---

## 1. What you need

### Tools
- Hot plate (or reflow oven / hot air station), tweezers, flux pen, solder paste
- Soldering iron (for connectors, switches, LEDs, resistors in hand-solder case)
- Loupe / microscope (QFN + fine pitch), multimeter (continuity)
- Stencil + paste printer (optional; you can dispense paste manually)

### Consumables
- Solder paste (SAC305 lead-free, or Sn42Bi58 if you want lower temp)
- Flux (no-clean preferred), solder wire (for through-hole parts), IPA for cleaning

## 2. Order of assembly — "risky IC last"

For best rework behavior (from the SMD hot-plate skill):

1. **Small passives first:** C1–C15, R1–R17, F1, FB1
2. **LEDs D4–D19** (mind polarity!)
3. **ICs:** USBLC6 (U2), AP2112K (U6), BQ24075 (U1) — QFN with ~50% paste on thermal pad
4. **ESP32-S3 module (U3) — last IC** if you expect rework: fresh paste, never sees two
   full-board reflows. If you're confident, it can also go with the other ICs.
5. **Power/mechanical:** USB-C (J2), headers (J4, J5), battery connector (J6), slide
   switch (S1), tact buttons SW1/SW2
6. **Key switches** (SW3–SW11, SW13, SW14) + rotary encoder (SW12)
7. Soldered-in-place LEDs (from through-hole: D16 is blue etc.) — align with the overlay

## 3. Hot plate reflow profile (SAC305)

- Solder melts at **217 °C**. Plate setpoint ≈ **250 °C max** (ESP32 module peak
  235–250 °C, max 3 reflow cycles).
- **Soak:** plate at **~150 °C**, board on top, **60–90 s**. Start timing when the plate
  *reaches* 150 °C, not at switch-on.
- **Reflow:** raise to **~250 °C**, watch paste turn shiny and ICs sink into place
  (usually 30–60 s). I did 260 °C, since my hot plate's temperature doesn't match the pcb temperature. But be carefull not to destroy your ESP32.
- **Cool-down:** kill the heat (or slide the board onto a wooden block). **Never quench**
  (no compressed air / wet cloth) — thermal shock cracks QFN and ceramic caps.
- Total plate time ~2–3.5 min; **time above 217 °C under ~90 s**.

> If solder won't melt until 260 °C: that's an **insufficient soak or bad plate contact**,
> not a need for more heat. Board must sit flat; use an aluminum heat-spreader plate if
> the hot plate is smaller than the board.

## 4. BQ24075 (VQFN-16, bottom pad) — the tricky one

- **~50 % paste coverage** on the thermal pad (thin cross or sparse dots). Full coverage
  lifts the chip so the side pads can't wet.
- The bottom pad is often **electrically GND** — a weak joint gives a dead circuit, not
  just heat creep.
- After reflow: chip sits **flush, no tilt**, tiny fillet around the pad edge. Verify all
  16 pads wetted under the loupe.
- 0.5 mm pitch QFN self-aligns with flux — nudge with tweezers while molten.

## 5. ESP32-S3 module (U3)

- Large castellated module — paste the pads, place carefully with pin 1 orientation.
- Peak limit 235–250 °C; keep it to max **3 reflow cycles** total.
- After soldering, check the USB-Serial/JTAG still enumerates (`lsusb` shows
  `303a:1001`); see docs/FIRMWARE.md.

## 6. Through-hole & mechanical parts

- **Key switches:** insert through the board/plate and solder pins (flat on the plate).
- **Rotary encoder SW12:** 5 pins (A/B/C + 2 switch pins); mind the orientation (pin 1
  marker).
- **LEDs:** 3 mm; check polarity (flat side = cathode). D16 blue = status, the rest green/
  orange by overlay.
- **Solder jumpers J9–J12:** default bridged per design intent (read the silkscreen).

## 7. Post-solder verification checklist

1. **Visual:** bridges between fine-pitch pads (loupe), tilted QFN, solder balls.
2. **Continuity:** supply rails — 0 Ω from USB 5 V node to fuse output; GND all around.
3. **Keyswitches** (best after assembly): every key open unpressed, <1 Ω pressed.
   Cold joints under plastic switch bodies are the #1 reflow casualty.
4. **Boot test:** connect USB-C → `/dev/ttyACM0` appears, `esptool` identifies the
   ESP32-S3, upload the blink test. LED on GPIO9 should blink.

See also [docs/HARDWARE.md](HARDWARE.md) for the component list and datasheets.
