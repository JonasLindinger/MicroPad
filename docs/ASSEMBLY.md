# MicroPad — Assembly Guide

> ⚠️ **AI-assisted documentation** — reviewed by the author, provided as-is, without
> warranty. Follow at your own risk.

How to put the finished PCB into the 3D-printed case and end up with a working, nice
looking keypad. **Prerequisite:** the board is soldered and passes the boot test
(see [SOLDERING.md](SOLDERING.md)).

---

## 1. Printed parts

Print all STL files from `STL Files/`:

| File | Purpose | Suggested settings |
|---|---|---|
| `Case.stl` | Bottom/lower shell | PETG or PLA+, 0.2 mm, ~25 % infill |
| `Top.stl` | Top frame (holds keycaps, screen bezel) | PETG or PLA+, 0.2 mm |
| `Keycap.stl` | Keycaps for the switches | PETG, 0.12 mm for crisp caps |
| `Knob.stl` | Rotary encoder knob | PETG, 0.12 mm (snug fit) |
| `E-Ink Display mount.stl` | Display holder/frame | PETG/PLA+ |

Tips:
- Print the **Keycap set** as many times as you have keys (11 printed keys + 1 encoder
  knob; you may also skip printed keycaps and use real Cherry MX keycaps).
- Check dimensional fit on a spare part before committing to a full set.
- Optional: print in color/transparent filament; the display mount usually hides the
  e-paper PCB edges.

## 2. Required hardware (off-PCB)

- 4× M3 screws (length matched to case thickness, e.g. M3×8–12)
- 4× M3 heat-set inserts (or self-tapping M3 screws into printed holes)
- Optional: rubber feet, battery (JST-PH 2-pin connector, 1S Li-Ion ~3.7 V)

## 3. Assembly steps

1. **Insert heat-set inserts** into the base (`Case.stl`) at the 4 M3 positions (if your
   case model uses them; follow the model's tolerance).
2. **Mount the e-paper display** into/against `E-Ink Display mount.stl`, route the FFC/flex
   toward header **J5**.
   - Careful: e-paper flex connectors are fragile — insert straight, close the latch once.
3. **Connect the display to J5** (pin order on the header: 3V3, GND, MOSI/MISO?, CLK, CS,
   DC, RST, BUSY — confirm against silkscreen; see HARDWARE.md).
4. **Place the PCB** into the case; align USB-C cutout, switch openings and encoder shaft.
5. **Screw the board down** with the M3 hardware.
6. **Install keycaps** on the switches. Optionally use printed `Keycap.stl`.
7. **Press the encoder knob** onto the shaft (rotate while pressing to align the flat).
8. **Close with `Top.stl`** — it should snap/screw over the keycaps and screen bezel.
9. **Insert the battery** via JST-PH (J6) — expect a small click. Route the wires so they
   don't pinch on the slide switch.
10. **Power on** via USB-C or the slide switch **S1**. You should see the e-paper
    initialize (screen flashes) and, after flashing the firmware, the WiFi setup or menu.

## 4. Calibration / first run

- Flash the v3 firmware first (docs/FIRMWARE.md).
- On first boot the pad shows the **MicroPad-Setup** AP (pass `micropad123`). Connect,
  enter WiFi SSID/password + MQTT broker (HA host IP).
- After HA has the micropad automation + pages published, the pad shows the **home menu**
  (it fetches all pages at boot).
- Test: rotary encoder scrolls, ENTER opens, BACK returns, HOME goes to the root page.

## 5. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Screen stays white | Display flex not seated in J5 / broken latch; BUSY pin not wired |
| Buttons random / dead row/col | Cold solder joint on the switch; see SOLDERING.md |
| AP "MicroPad-Setup" doesn't appear | No saved WiFi config + firmware expects it; hold SETTINGS |
| No boot after battery insert | Slide switch S1 off; battery polarity on J6 |
| Rotary does nothing | Knob not on shaft / soldered A-B swapped |
| Wakes but display stale | Normal — e-paper keeps image; interact to redraw |

## 6. Final check

A list of what to prepare for prints/bom:

- 11× keycaps (printed or Cherry MX compatible)
- 1× encoder knob + 1× display mount
- 1× case + 1× top