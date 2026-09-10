# MicroPad Hardware & Pinout

> ⚠️ **AI-assisted documentation** — pin data verified against the KiCad PCB netlist and
> the firmware, but double-check against your own board before wiring anything. Provided
> as-is, without warranty.

This document describes the printed circuit board, all components, and the exact pin
assignment (verified from the KiCad PCB netlist against the firmware).

---

## 1. Overview

- **Board size:** ≈ 84 × 91 mm, 4× M3 mounting holes
- **MCU:** ESP32-S3-WROOM-1 (dual core, 240 MHz, 8 MB PSRAM)
- **Display:** Waveshare-style 2.9" e-paper, 296×128 px, B/W — connected on header **J5**
- **Input:** 3×4 keyswitch matrix (11× Cherry MX compatible + 1 encoder push), rotary
  encoder **SW12** (EC11-style, with integrated push)
- **Power:** USB-C in, single-cell Li-Ion via charger, slide power switch, LDO 3.3 V

## 2. Block diagram

```
USB-C (J2) ── 2A fuse F1 ── ESD (USBLC6) ── ESP32-S3-WROOM-1 (U3)
                     │                            │
                   D1 1N5822                      ├─ SPI → e-paper (J5): CLK, MOSI, CS, DC, RST, BUSY
                     │                            ├─ 3×4 key matrix: R1..R3 (rows), C1..C4 (cols)
              BQ24075 charger (U1)               ├─ Rotary encoder SW12: A, B (+ push on matrix)
                     │                            └─ LEDs / UART / USB
                  J6 battery                     │
                (JST-PH 2-pin)              AP2112K-3.3 LDO (U6) → +3V3
```

## 3. Main components (from BOM)

| Ref | Part | Function |
|---|---|---|
| U3 | ESP32-S3-WROOM-1 | Main MCU (WiFi/BT, 8 MB PSRAM) |
| U1 | BQ24075RGT | Li-Ion linear charger (VQFN-16, thermal pad) |
| U6 | AP2112K-3.3 | 3.3 V LDO (SOT-23-5) |
| U2 | USBLC6-2SC6 | USB ESD protection (SOT-23-6) |
| J2 | USB-C receptacle (GCT USB4085) | USB power + native USB-Serial/JTAG |
| J5 | 1×8 pin header | E-paper SPI connector |
| J4 | 2×3 pin header | Programming/UART header |
| J6 | JST-PH 2-pin (B2B-PH-K-S) | Li-Ion battery |
| SW12 | Rotary encoder with push | Navigation + enter |
| SW3–SW11, SW13, SW14 | Cherry MX compatible | Key matrix (11×) |
| SW1 / SW2 | 6 mm tact | RESET / BOOT |
| S1 | Slide switch | Power on/off |
| D16–D19 | 3 mm LEDs (blue/green/orange) | Status/charge LEDs |
| F1 | 2 A fuse | Input protection |
| J9–J12 | Solder jumpers | LED selection options |

## 4. ESP32-S3 pin assignment (verified from PCB netlist)

The table maps **GPIO → function → net name**. `R` = matrix row, `C` = matrix column.

### Display — SPI e-paper (header J5, 1×8: 3V3, GND, MOSI, CLK, CS, DC, RST, BUSY)

| GPIO | Function | Net | Firmware const |
|---|---|---|---|
| 39 | SPI SCK | /CLK | `SPI.begin(39, -1, 38)` |
| 38 | SPI MOSI (DIN) | /MOSI | (MISO unused) |
| 8 | CS (chip select) | /CS | `EPD_CS   8` |
| 40 | DC (data/command) | /DC | `EPD_DC  40` |
| 41 | RST (reset) | /RST | `EPD_RST 41` |
| 42 | BUSY | /BUSY | `EPD_BUSY 42` |

### Key matrix — 3 rows × 4 columns (active low)

Matrix pins drive the **keyboard switches** (11 Cherry MX + encoder push). Columns have
`INPUT_PULLUP`; a pressed key reads LOW.

| GPIO | Function | Net | Firmware |
|---|---|---|---|
| 47 | Row 1 | /R1 | `ROWS[0]` |
| 48 | Row 2 | /R2 | `ROWS[1]` |
| 45 | Row 3 | /R3 | `ROWS[2]` |
| 21 | Column 1 | /C1 | `COLS[0]` |
| 14 | Column 2 | /C2 | `COLS[1]` |
| 13 | Column 3 | /C3 | `COLS[2]` |
| 12 | Column 4 | /C4 | `COLS[3]` |

**Button mapping** (coordinates `row/col`):

| Button | Matrix | Firmware |
|---|---|---|
| ENTER | R1C3 | `BTN_ENTER` |
| ENTER (2nd / encoder push) | R0C3 | `BTN_ENTER2` |
| BACK | R2C3 | `BTN_BACK` |
| HOME | R0C0 | `BTN_HOME` |
| SETTINGS | R2C2 | `BTN_SETTINGS` |

### Rotary encoder SW12 (EC11 style)

| GPIO | Function | Net | Firmware |
|---|---|---|---|
| 10 | Encoder A | /A | `ENC_A` |
| 11 | Encoder B | /B | `ENC_B` |
| — | Encoder push | /C4 (matrix) | part of matrix (ENTER2) |

### Power, LEDs, USB, misc

| GPIO / pin | Function | Net | Note |
|---|---|---|---|
| USB-D− / USB-D+ | Native USB | /USB_D−, /USB_D+ | USB-Serial/JTAG via USBLC6 |
| 9 | **Control LED** | /CTRL LED | Status LED, solder-jumper selectable (J9–J12) |
| 0 | BOOT button | /BOOT | Strapping pin; also on J4 |
| EN | Enable/reset | /EN | RESET button SW1, also on J4 |
| 43 | UART TX | /TXD | J4 pin 3 |
| 44 | UART RX | /RXD | J4 pin 5 |
| 3 | GPIO3 | /IO3 | Free/strapping |
| 1, 2, 4–7, 15–18, 35–37, 46 | — | unconnected | Free for expansion |

### Programming header J4 (2×3, 2.54 mm)

| Pin | Net | Purpose |
|---|---|---|
| 1 | /EN | Reset line (RESET) |
| 2 | +3V3 | Power (probe) |
| 3 | /TXD | UART TX (GPIO43) |
| 4 | GND | Ground |
| 5 | /RXD | UART RX (GPIO44) |
| 6 | /BOOT | Boot select (BOOT) |

---

## 5. Power architecture

- **USB 5 V** → fuse **F1 (2 A)** → ferrite **FB1** → reverse diode **D1 (1N5822)**
- **BQ24075 (U1)** charges the Li-Ion cell (battery on **J6**, JST-PH)
- **AP2112K-3.3 (U6)** regulates 3.3 V for the logic
- Slide switch **S1** switches power path PWR S1/S2
- Charge/prog LEDs D16–D19 (jumper-selectable on J9–J12)

## 6. Datasheet & further docs (links)

| Part | Link |
|---|---|
| ESP32-S3-WROOM-1 (module) | https://www.espressif.com/sites/default/files/documentation/esp32-s3-wroom-1_wroom-1n_datasheet_en.pdf |
| ESP32-S3 datasheet | https://www.espressif.com/sites/default/files/documentation/esp32-s3_datasheet_en.pdf |
| ESP32-S3 TRM (technical reference) | https://www.espressif.com/sites/default/files/documentation/esp32-s3_technical_reference_manual_en.pdf |
| BQ24075 charger | https://www.ti.com/product/BQ24075 |
| AP2112K LDO | https://www.diodes.com/assets/Datasheets/AP2112.pdf |
| USBLC6-2SC6 ESD | https://www.st.com/resource/en/datasheet/usblc6-2sc6.pdf |
| Waveshare 2.9" e-paper (display family) | https://www.waveshare.com/wiki/2.9inch_e-Paper_Module |
| GxEPD2 library (display driver) | https://github.com/ZinggJM/GxEPD2 |
| Cherry MX datasheet | https://www.cherrymx.de/en/dev.html |
| EC11 rotary encoder (series) | https://www.alpsalpine.com/products/datasheet/EC11.pdf |
| GCT USB4085 USB-C | https://www.gct.co/connector/usb4085 |
| JST B2B-PH-K-S (battery) | https://www.jst-mfg.com/product/detail_e.php?series=199 |

## 7. Expansion potential

Free GPIOs (1, 2, 4–7, 15–18, 35–37, 46) allow adding sensors (I2C: GPIO1/2 has
internal pullups usable as SDA/SCL), an RGB LED, haptics, or a second rotary encoder.
I2C-capable pins on ESP32-S3: GPIO1/2 (with pull-ups), GPIO3, 4, 5 exists as I2C — verify
against the port map in the TRM before use. GPIO35–37 are also ADC1 capable for e.g. a
battery voltage divider.
