# MicroPad — Firmware & HA Integration

> ⚠️ **AI-assisted:** the firmware, the Home Assistant automation and this documentation
> were developed with AI (LLM) assistance and reviewed/tested by the author. Provided
> as-is, no warranty — verify on your own hardware.

The pad is a battery-efficient ESP32-S3 controller. The menu is **owned by Home
Assistant** — the device just renders pages sent over MQTT, so you never reflash to
change the menu.

---

## 1. Firmware requirements

- Arduino IDE + **esp32** board package (v2/v3; the project was built with 3.x)
- Libraries:
  - `GxEPD2` (e-paper driver)
  - `PubSubClient` (MQTT)
  - `ArduinoJson` (>= 6)
  - `WiFi`, `WebServer`, `Preferences`, `esp_task_wdt` (bundled with ESP32 core)

## 2. Flashing

**Arduino IDE**

1. Open `firmware/MicroPad_HA_Controller_v3.ino`.
2. Board: **ESP32-S3** (use your board config; features: dual core, 8 MB PSRAM).
3. **Port:** native USB-Serial/JTAG (`/dev/ttyACM0` / COM port).
4. **USB CDC On Boot: Enabled** (so serial debug appears over USB).
5. Upload. The pad will boot into the WiFi setup AP (`MicroPad-Setup`/`micropad123`) on
   first run and store your network + MQTT broker in NVS.

> Some boards' native USB needs the board in **download mode**: hold BOOT, tap RESET,
> release — then upload.

**CLI (arduino-cli)**

```sh
arduino-cli compile --fqbn 'esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc' firmware_dir
arduino-cli upload -p /dev/ttyACM0 --fqbn 'esp32:esp32:esp32s3' firmware_dir
```

## 3. Power management

- After **60 s without input** the pad enters **light sleep**: WiFi & MQTT disconnect,
  display keeps its image, wake on **any key / encoder movement**.
- On wake: reconnects WiFi+MQTT, re-requests the current page automatically.
- `didFetchAllPages` guard: the full page catalog is fetched **once per boot**, not on
  every wake — the per-page cache stays fresh via normal event round trips.

## 4. MQTT protocol

Credentials are stored in NVS (broker host editable via the setup portal). Default user:
`micropad`.

### Pad → HA: topic `micropad/event`

Events are queued (small FIFO) and flushed on the MQTT loop. JSON:

```json
{"action":"home","page_id":"home"}
{"action":"navigate","target_page":"lamps","page_id":"home"}
{"action":"toggle","entity":"light.living_room","page_id":"lamps"}
{"action":"press","entity":"script.start_movie","page_id":"cinema"}
{"action":"edit","entity":"number.x1c_..._nozzle_target_temperature","value":200,"page_id":"printer"}
{"action":"get_all_pages","page_id":""}
```

Actions supported: `home`, `navigate`, `toggle`, `press`, `edit`, `get_all_pages`.

### HA → Pad: topic `micropad/page/current`

A page looks like:

```json
{
  "page_id": "printer",
  "title": "3D Drucker",
  "parent": "home",
  "items": [
    {"name":"Printer power","type":"switch","entity":"switch.smart_switch_...","state":"off"},
    {"name":"Nozzle target","type":"number","entity":"number.x1c_..._nozzle","value":"0","min":0,"max":300,"step":5,"editable":true},
    {"name":"Lamps","type":"category","target_page":"lamps"}
  ]
}
```

Item types: `category`, `light`, `switch`, `script`, `button`, `sensor`,
`media_player`, `number`, `settings`, `back`.

- `light`/`switch` → ENTER sends `toggle`
- `script`/`button` → ENTER sends `press`
- `number`/`media_player` (or `editable:true`) → ENTER opens the rotary-encoder editor,
  ENTER again sends `edit` with the chosen value
- `category` → navigate; `settings` → WiFi portal; `back` → go to `parent`
- `state` shows next to the name for lights/switches/sensors; `value` for
  numbers/media players

### HA → Pad: topic `micropad/pages/all` (boot catalog)

One retained message with every page, so navigation is instant without per-page round
trips:

```json
{"pages":[ {page-home}, {page-lamps}, ... ]}
```

## 5. Home Assistant automation

A single automation "MicroPad Controller" (id `micropad_controller`) handles all events.
The **Web Config Generator** builds it for you (see below); a hand-written version lives
in the repo fixture `configuration-examples.yaml`.

Branches: `home`/`navigate` (publish page), `toggle` (toggle + republish page),
`press`, `edit` (number/input_number/media_player), `get_all_pages` (publish catalog).

Note: `number.*` entities need `number.set_value`, `input_number.*` need
`input_number.set_value` — the generator emits both branches.

## 6. Hints & limits (firmware-enforced)

- **Max 20 items per page** (`MenuItem items[20]`); extras are dropped.
- **MQTT buffer 2048 B** → keep per-page JSON ≤ ~1800 B. The generator validates this.
- **`home` page must exist** — the pad requests it on boot; without it the pad hangs on
  "Loading…".
- `light`/`switch` can't enter the encoder editor (they always `toggle` first) — use a
  `number` item (input_number) for dimming.

## 7. Web Config Generator

A Flask web app (separate folder / zip) provides a modern UI to:

- manage pages/items with live searchable HA entity list (friendly name + entity id)
- test the HA connection, fetch entities
- generate the automation YAML
- upload via HA Config API (preferred) or SSH merge into `automations.yaml`
- download the current config back from HA
- load the currently active pad page

Run (container or anywhere with Python 3):

```sh
pip install -r requirements.txt
MICROPAD_PORT=8080 python app.py
# → http://<host>:8080
```